"""
Code Indexer — chunks code files, embeds with Ollama, stores in Milvus Lite.
Provides semantic search over the entire codebase.
"""
import os
import hashlib
import json
import requests
from pathlib import Path
from pymilvus import MilvusClient
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

EMBEDDING_DIM = 768  # nomic-embed-text default dimension


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
        # API returns {"embeddings": [[...]]}
        emb = data.get("embeddings", [[]])[0]
        embeddings.append(emb)
    return embeddings


class CodeIndexer:
    """Chunks, embeds, and indexes code files using Milvus Lite + Ollama."""

    COLLECTION = "codebase"
    META_FILE = "index_meta.json"

    def __init__(
        self,
        workspace: str = None,
        db_path: str = None,
        chunk_size: int = 80,
        chunk_overlap: int = 20,
    ):
        self.workspace = workspace or config.WORKSPACE_DIR
        self.db_path = db_path or "http://localhost:19530"
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.embedding_model = getattr(config, "EMBEDDING_MODEL", "nomic-embed-text")

        # Connect to Milvus standalone (Docker port 19530)
        self._client = MilvusClient(uri=self.db_path)

        # Create collection if not exists
        if not self._client.has_collection(self.COLLECTION):
            from pymilvus import DataType, CollectionSchema, FieldSchema

            fields = [
                FieldSchema("id", DataType.VARCHAR, is_primary=True, max_length=512),
                FieldSchema("vector", DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
                FieldSchema("file", DataType.VARCHAR, max_length=512),
                FieldSchema("lang", DataType.VARCHAR, max_length=32),
                FieldSchema("start_line", DataType.INT64),
                FieldSchema("end_line", DataType.INT64),
                FieldSchema("text", DataType.VARCHAR, max_length=65535),
            ]
            schema = CollectionSchema(fields)
            self._client.create_collection(
                collection_name=self.COLLECTION,
                schema=schema,
            )
            # Create vector index for fast search
            index_params = self._client.prepare_index_params()
            index_params.add_index(
                field_name="vector",
                index_type="FLAT",
                metric_type="COSINE"
            )
            self._client.create_index(
                collection_name=self.COLLECTION,
                index_params=index_params
            )
            
        self._client.load_collection(self.COLLECTION)

        # File hash tracking (simple JSON file)
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
            stats = self._client.get_collection_stats(self.COLLECTION)
            return stats.get("row_count", 0)
        except Exception:
            return 0

    def index_workspace(self, force: bool = False) -> dict:
        """Scan and index all code files in workspace.

        Args:
            force: Re-index all files if True. Otherwise only new/modified.

        Returns:
            Stats dict.
        """
        stats = {"indexed": 0, "skipped": 0, "errors": 0, "total_chunks": 0}

        for root, dirs, files in os.walk(self.workspace):
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in BINARY_EXT:
                    continue

                fpath = os.path.join(root, fname)
                rel_path = os.path.relpath(fpath, self.workspace).replace("\\", "/")

                try:
                    result = self._index_file(rel_path, fpath, force)
                    if result == "indexed":
                        stats["indexed"] += 1
                    elif result == "skipped":
                        stats["skipped"] += 1
                except Exception as e:
                    stats["errors"] += 1
                    print(f"  ⚠️ Error indexing {rel_path}: {e}")

        self._save_meta()
        stats["total_chunks"] = self.total_chunks
        return stats

    def _index_file(self, rel_path: str, abs_path: str, force: bool) -> str:
        """Index a single file."""
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except (PermissionError, OSError):
            return "skipped"

        if not content.strip():
            return "skipped"

        file_hash = hashlib.md5(content.encode()).hexdigest()

        # Skip unchanged files
        if not force and rel_path in self._file_hashes:
            if self._file_hashes[rel_path] == file_hash:
                return "skipped"

        # Remove old chunks
        self._delete_file_chunks(rel_path)

        # Chunk and embed
        chunks = self._chunk_file(content, rel_path)
        if not chunks:
            return "skipped"

        # Get embeddings from Ollama
        texts = [c["text"] for c in chunks]
        try:
            embeddings = _ollama_embed(texts, self.embedding_model)
        except Exception as e:
            raise RuntimeError(f"Embedding failed for {rel_path}: {e}")

        # Insert into Milvus
        data = []
        for i, chunk in enumerate(chunks):
            data.append({
                "id": f"{rel_path}::chunk_{i}",
                "vector": embeddings[i],
                "file": rel_path,
                "lang": chunk["lang"],
                "start_line": chunk["start_line"],
                "end_line": chunk["end_line"],
                "text": chunk["text"][:65535],
            })

        # Batch insert
        batch = 100
        for i in range(0, len(data), batch):
            self._client.insert(
                collection_name=self.COLLECTION,
                data=data[i:i + batch],
            )

        self._file_hashes[rel_path] = file_hash
        return "indexed"

    def _get_logical_blocks(self, lines: list[str], ext: str) -> list[tuple[str, int, int]]:
        """Find logical blocks (classes, functions) for better RAG chunking."""
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
                match = class_pattern.search(sline) or func_pattern.search(sline) or const_func_pattern.search(sline)
                if match:
                    name = match.group(0).split('(')[0].split('=')[0].split('{')[0].strip()
                    if name.startswith("export"): name = name.replace("export", "", 1).strip()
                    if name.startswith("default"): name = name.replace("default", "", 1).strip()
                    anchors.append((name, i))
                    
            for i, (name, start) in enumerate(anchors):
                end = anchors[i+1][1] - 1 if i + 1 < len(anchors) else len(lines) - 1
                blocks.append((name, start, end))
                
        return blocks

    def _chunk_file(self, content: str, rel_path: str) -> list[dict]:
        """Split file into AST-aware overlapping chunks."""
        lines = content.split("\n")
        total = len(lines)
        ext = os.path.splitext(rel_path)[1].lower()
        lang = self._detect_lang(ext)

        blocks = self._get_logical_blocks(lines, ext)
        chunks = []
        start = 0

        while start < total:
            # Determine contextual block name
            block_name = "Global scope"
            for name, b_start, b_end in blocks:
                if b_start <= start <= b_end:
                    block_name = name
                    break
                    
            end = min(start + self.chunk_size, total)
            
            # Snap to next logical boundary if it's within the chunk window
            next_anchor = next((b_start for n, b_start, b_e in blocks if start < b_start < end), None)
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

            # Advance carefully ensuring we always move forward, applying overlap
            advance = max(1, (end - start) - self.chunk_overlap)
            start += advance

        return chunks

    def search(
        self,
        query: str,
        n_results: int = 10,
        file_filter: str = None,
        lang_filter: str = None,
    ) -> list[dict]:
        """Semantic search over indexed codebase.

        Args:
            query: Natural language query.
            n_results: Max results.
            file_filter: Optional file path filter (partial match).
            lang_filter: Optional language filter.

        Returns:
            List of results with file, lines, content, score.
        """
        # Embed the query
        try:
            query_embedding = _ollama_embed([query], self.embedding_model)[0]
        except Exception as e:
            return [{"error": f"Embedding failed: {e}"}]

        # Build filter
        filter_expr = None
        if lang_filter:
            filter_expr = f'lang == "{lang_filter}"'
        if file_filter:
            file_f = f'file like "%{file_filter}%"'
            filter_expr = f"{filter_expr} and {file_f}" if filter_expr else file_f

        try:
            results = self._client.search(
                collection_name=self.COLLECTION,
                data=[query_embedding],
                limit=n_results,
                output_fields=["file", "lang", "start_line", "end_line", "text"],
                filter=filter_expr,
            )
        except Exception as e:
            return [{"error": f"Search failed: {e}"}]

        hits = []
        if not results or not results[0]:
            return hits

        for hit in results[0]:
            entity = hit.get("entity", {})
            hits.append({
                "file": entity.get("file", ""),
                "start_line": entity.get("start_line", 0),
                "end_line": entity.get("end_line", 0),
                "lang": entity.get("lang", ""),
                "content": entity.get("text", ""),
                "score": round(hit.get("distance", 0), 4),
            })

        return hits

    def _delete_file_chunks(self, rel_path: str):
        """Remove all chunks for a file."""
        try:
            self._client.delete(
                collection_name=self.COLLECTION,
                filter=f'file == "{rel_path}"',
            )
        except Exception:
            pass

    def remove_file(self, rel_path: str):
        self._delete_file_chunks(rel_path)
        self._file_hashes.pop(rel_path, None)
        self._save_meta()

    def clear(self):
        """Drop and recreate collection."""
        try:
            self._client.drop_collection(self.COLLECTION)
        except Exception:
            pass
        self._file_hashes = {}
        self._save_meta()
        self.__init__(self.workspace, self.db_path)

    def get_stats(self) -> dict:
        return {
            "total_chunks": self.total_chunks,
            "total_files": len(self._file_hashes),
            "workspace": self.workspace,
            "db_path": self.db_path,
            "embedding_model": self.embedding_model,
        }

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


# ── Singleton ───────────────────────────────────────────────
_indexer: CodeIndexer | None = None


def get_indexer() -> CodeIndexer:
    global _indexer
    if _indexer is None:
        _indexer = CodeIndexer()
    return _indexer
