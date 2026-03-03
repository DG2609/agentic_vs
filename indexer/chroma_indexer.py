"""
ChromaDB-backed code indexer — lightweight alternative to Milvus.

Requires: pip install chromadb
No Docker required — uses persistent file-based storage.

Same public API as CodeIndexer (index_workspace, search, total_chunks, etc.)
so semantic.py can swap backends transparently.
"""
import hashlib
import json
import os
import requests
from pathlib import Path

import config

IGNORE_DIRS = {
    "__pycache__", ".git", ".svn", "node_modules", ".venv", "venv",
    "env", "dist", "build", ".next", ".cache", ".tox",
    "target", "bin", "obj", ".idea", ".vscode",
}

BINARY_EXT = {
    ".exe", ".dll", ".so", ".o", ".obj", ".bin", ".dat", ".db",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".zip", ".tar", ".gz", ".rar", ".7z",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt",
    ".pyc", ".class", ".whl",
    ".mp3", ".mp4", ".avi", ".wav",
}

CODE_EXT = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".c", ".cpp", ".h", ".hpp",
    ".java", ".go", ".rs", ".m", ".xml", ".json", ".yaml", ".yml",
    ".toml", ".cfg", ".ini", ".md", ".txt", ".csv", ".sql",
    ".html", ".css", ".sh", ".bat", ".ps1", ".r", ".lua",
    ".rb", ".php", ".swift", ".kt", ".scala", ".pl",
}


def _ollama_embed(texts: list[str], model: str = None, base_url: str = None) -> list[list[float]]:
    """Get embeddings from Ollama API."""
    model = model or getattr(config, "EMBEDDING_MODEL", "nomic-embed-text")
    base_url = base_url or getattr(config, "OLLAMA_BASE_URL", "http://localhost:11434")

    embeddings = []
    for text in texts:
        resp = requests.post(
            f"{base_url}/api/embed",
            json={"model": model, "input": text},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        emb = data.get("embeddings", [[]])[0]
        embeddings.append(emb)
    return embeddings


class ChromaIndexer:
    """Chunks, embeds, and indexes code files using ChromaDB + Ollama.

    Storage: data/chroma_db/ (persistent, file-based — no Docker needed)
    """

    COLLECTION = "codebase"
    META_FILE = "chroma_meta.json"

    def __init__(
        self,
        workspace: str = None,
        chunk_size: int = 80,
        chunk_overlap: int = 20,
    ):
        try:
            import chromadb
        except ImportError:
            raise ImportError(
                "ChromaDB not installed. Run: pip install chromadb\n"
                "Or set VECTOR_BACKEND=milvus to use Milvus instead."
            )

        self.workspace = workspace or config.WORKSPACE_DIR
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.embedding_model = getattr(config, "EMBEDDING_MODEL", "nomic-embed-text")

        # Persistent storage in data/chroma_db/
        chroma_path = str(config.DATA_DIR / "chroma_db")
        os.makedirs(chroma_path, exist_ok=True)
        self._client = chromadb.PersistentClient(path=chroma_path)

        # Get or create collection (no fixed dim constraint with ChromaDB)
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )

        # File hash tracking
        self._meta_path = str(config.DATA_DIR / self.META_FILE)
        self._file_hashes = self._load_meta()

    def _load_meta(self) -> dict:
        if os.path.exists(self._meta_path):
            try:
                with open(self._meta_path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_meta(self):
        with open(self._meta_path, "w") as f:
            json.dump(self._file_hashes, f, indent=2)

    @property
    def total_chunks(self) -> int:
        try:
            return self._collection.count()
        except Exception:
            return 0

    def index_workspace(self, force: bool = False) -> dict:
        """Scan and index all code files in workspace.

        Writes live progress to indexer.INDEXING_PROGRESS so the server
        can stream index:progress events to connected clients.
        """
        from indexer import INDEXING_PROGRESS

        # Collect all candidate files first (for accurate total count)
        all_files = []
        for root, dirs, files in os.walk(self.workspace):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in BINARY_EXT:
                    all_files.append(os.path.join(root, fname))

        # Reset shared progress
        INDEXING_PROGRESS.update({
            "indexed": 0, "skipped": 0, "errors": 0,
            "total": len(all_files), "active": True,
        })

        stats = {"indexed": 0, "skipped": 0, "errors": 0, "total_chunks": 0}

        for fpath in all_files:
            rel_path = os.path.relpath(fpath, self.workspace).replace("\\", "/")
            try:
                result = self._index_file(rel_path, fpath, force)
                if result == "indexed":
                    stats["indexed"] += 1
                elif result == "skipped":
                    stats["skipped"] += 1
            except Exception:
                stats["errors"] += 1

            # Update shared progress after each file
            INDEXING_PROGRESS["indexed"] = stats["indexed"]
            INDEXING_PROGRESS["skipped"] = stats["skipped"]
            INDEXING_PROGRESS["errors"] = stats["errors"]

        self._save_meta()
        stats["total_chunks"] = self.total_chunks
        INDEXING_PROGRESS["active"] = False
        INDEXING_PROGRESS["total_chunks"] = stats["total_chunks"]
        return stats

    def _index_file(self, rel_path: str, abs_path: str, force: bool) -> str:
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except (PermissionError, OSError):
            return "skipped"

        if not content.strip():
            return "skipped"

        file_hash = hashlib.md5(content.encode()).hexdigest()

        if not force and rel_path in self._file_hashes:
            if self._file_hashes[rel_path] == file_hash:
                return "skipped"

        # Remove old chunks
        self._delete_file_chunks(rel_path)

        chunks = self._chunk_file(content, rel_path)
        if not chunks:
            return "skipped"

        texts = [c["text"] for c in chunks]
        try:
            embeddings = _ollama_embed(texts, self.embedding_model)
        except Exception as e:
            raise RuntimeError(f"Embedding failed for {rel_path}: {e}")

        ids = [f"{rel_path}::chunk_{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "file": rel_path,
                "lang": c["lang"],
                "start_line": c["start_line"],
                "end_line": c["end_line"],
            }
            for c in chunks
        ]

        # ChromaDB upsert in batches of 100
        batch = 100
        for i in range(0, len(chunks), batch):
            self._collection.upsert(
                ids=ids[i:i + batch],
                embeddings=embeddings[i:i + batch],
                documents=texts[i:i + batch],
                metadatas=metadatas[i:i + batch],
            )

        self._file_hashes[rel_path] = file_hash
        return "indexed"

    def search(
        self,
        query: str,
        n_results: int = 10,
        file_filter: str = None,
        lang_filter: str = None,
    ) -> list[dict]:
        """Semantic search over indexed codebase."""
        try:
            query_embedding = _ollama_embed([query], self.embedding_model)[0]
        except Exception as e:
            return [{"error": f"Embedding failed: {e}"}]

        # Build where clause
        where = {}
        if lang_filter and file_filter:
            where = {"$and": [{"lang": {"$eq": lang_filter}}, {"file": {"$contains": file_filter}}]}
        elif lang_filter:
            where = {"lang": {"$eq": lang_filter}}
        elif file_filter:
            where = {"file": {"$contains": file_filter}}

        try:
            kwargs = {
                "query_embeddings": [query_embedding],
                "n_results": min(n_results, self.total_chunks or 1),
                "include": ["documents", "metadatas", "distances"],
            }
            if where:
                kwargs["where"] = where

            results = self._collection.query(**kwargs)
        except Exception as e:
            return [{"error": f"Search failed: {e}"}]

        hits = []
        if not results or not results.get("documents") or not results["documents"][0]:
            return hits

        docs = results["documents"][0]
        metas = results["metadatas"][0]
        distances = results["distances"][0]

        for doc, meta, dist in zip(docs, metas, distances):
            # ChromaDB cosine distance: 0=identical, 2=opposite. Convert to similarity.
            similarity = round(1 - (dist / 2), 4)
            hits.append({
                "file": meta.get("file", ""),
                "start_line": meta.get("start_line", 0),
                "end_line": meta.get("end_line", 0),
                "lang": meta.get("lang", ""),
                "content": doc,
                "score": similarity,
            })

        return hits

    def _delete_file_chunks(self, rel_path: str):
        try:
            existing = self._collection.get(where={"file": {"$eq": rel_path}})
            if existing and existing.get("ids"):
                self._collection.delete(ids=existing["ids"])
        except Exception:
            pass

    def remove_file(self, rel_path: str):
        self._delete_file_chunks(rel_path)
        self._file_hashes.pop(rel_path, None)
        self._save_meta()

    def clear(self):
        try:
            self._client.delete_collection(self.COLLECTION)
        except Exception:
            pass
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        self._file_hashes = {}
        self._save_meta()

    def get_stats(self) -> dict:
        return {
            "total_chunks": self.total_chunks,
            "total_files": len(self._file_hashes),
            "workspace": self.workspace,
            "backend": "chromadb",
            "embedding_model": self.embedding_model,
        }

    # ── Chunking (reuse same logic as MilvusIndexer) ──────────

    def _get_logical_blocks(self, lines: list[str], ext: str) -> list[tuple]:
        import ast
        import re
        blocks = []

        if ext == ".py":
            try:
                content = "\n".join(lines)
                tree = ast.parse(content)
                for node in tree.body:
                    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        name = f"{'class' if isinstance(node, ast.ClassDef) else 'func'} {node.name}"
                        start = node.lineno - 1
                        end = getattr(node, "end_lineno", len(lines)) - 1
                        blocks.append((name, start, end))
            except Exception:
                pass

        elif ext in {".js", ".ts", ".jsx", ".tsx"}:
            class_pattern = re.compile(r'^(?:export\s+)?(?:default\s+)?class\s+([A-Za-z0-9_]+)')
            func_pattern = re.compile(r'^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z0-9_]+)')
            const_func_pattern = re.compile(r'^(?:export\s+)?const\s+([A-Za-z0-9_]+)\s*=\s*(?:async\s+)?(?:function|\()')
            anchors = []
            for i, line in enumerate(lines):
                sline = line.strip()
                m = class_pattern.search(sline) or func_pattern.search(sline) or const_func_pattern.search(sline)
                if m:
                    name = m.group(0).split('(')[0].split('=')[0].strip()
                    anchors.append((name, i))
            for i, (name, start) in enumerate(anchors):
                end = anchors[i + 1][1] - 1 if i + 1 < len(anchors) else len(lines) - 1
                blocks.append((name, start, end))

        return blocks

    def _chunk_file(self, content: str, rel_path: str) -> list[dict]:
        lines = content.split("\n")
        total = len(lines)
        ext = os.path.splitext(rel_path)[1].lower()
        lang = self._detect_lang(ext)
        blocks = self._get_logical_blocks(lines, ext)
        chunks = []
        start = 0

        while start < total:
            block_name = "Global scope"
            for name, b_start, b_end in blocks:
                if b_start <= start <= b_end:
                    block_name = name
                    break

            end = min(start + self.chunk_size, total)
            next_anchor = next(
                (b_start for n, b_start, b_e in blocks if start < b_start < end), None
            )
            if next_anchor and (next_anchor - start >= 10):
                end = next_anchor

            chunk_lines = lines[start:end]
            chunk_text = "\n".join(chunk_lines)
            header = f"File: {rel_path} | Language: {lang} | Context: {block_name} | Lines: {start + 1}-{end}"
            full_text = f"{header}\n\n{chunk_text}"

            chunks.append({
                "text": full_text,
                "lang": lang,
                "start_line": start + 1,
                "end_line": end,
            })

            advance = max(1, (end - start) - self.chunk_overlap)
            start += advance

        return chunks

    @staticmethod
    def _detect_lang(ext: str) -> str:
        langs = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".jsx": "jsx", ".tsx": "tsx", ".c": "c", ".cpp": "cpp",
            ".h": "c-header", ".hpp": "cpp-header", ".java": "java",
            ".go": "go", ".rs": "rust", ".m": "matlab", ".xml": "xml",
            ".json": "json", ".yaml": "yaml", ".yml": "yaml",
            ".md": "markdown", ".html": "html", ".css": "css",
            ".sql": "sql", ".sh": "bash", ".r": "r", ".lua": "lua",
        }
        return langs.get(ext, "text")
