"""
Tools: semantic_search and index_codebase — powered by Milvus + Ollama embeddings.
All outputs go through universal truncation.
"""
from langchain_core.tools import tool
from indexer.code_indexer import get_indexer
from agent.tools.truncation import truncate_output
from models.tool_schemas import SemanticSearchArgs, IndexCodebaseArgs


@tool(args_schema=SemanticSearchArgs)
def semantic_search(
    query: str,
    n_results: int = 8,
    file_filter: str = "",
    lang_filter: str = "",
) -> str:
    """Search the codebase by meaning using vector similarity (semantic search).

    Unlike keyword search, this finds code that is conceptually related to your query
    even if the exact words don't appear. For example, searching "error handling" will
    find try/catch blocks, exception classes, etc.

    The codebase must be indexed first (use index_codebase tool).

    Args:
        query: Natural language description of what you're looking for.
        n_results: Max number of code chunks to return. Default 8.
        file_filter: Optional partial file path to narrow search (e.g. "utils" or "test").
        lang_filter: Optional language filter (e.g. "python", "c", "matlab").

    Returns:
        Relevant code chunks ranked by similarity, with file paths and line numbers.
    """
    indexer = get_indexer()

    if indexer.total_chunks == 0:
        return "⚠️ Codebase is not indexed yet. Use the `index_codebase` tool first."

    results = indexer.search(
        query=query,
        n_results=n_results,
        file_filter=file_filter or None,
        lang_filter=lang_filter or None,
    )

    if not results:
        return f"No semantic matches found for: '{query}'"

    if results and "error" in results[0]:
        return f"Search error: {results[0]['error']}"

    output = [f"🔍 Semantic search: \"{query}\" ({len(results)} results)\n"]

    for i, hit in enumerate(results, 1):
        score_pct = f"{hit['score'] * 100:.1f}%" if hit.get('score') else "N/A"
        output.append(f"{'─' * 50}")
        output.append(f"#{i}  📄 {hit['file']}  L{hit['start_line']}-{hit['end_line']}  ({hit['lang']})  Score: {score_pct}")
        output.append(f"{'─' * 50}")

        # Show the code content (strip the metadata header)
        content = hit.get("content", "")
        lines = content.split("\n")
        # Skip the header line(s) we prepended during indexing
        code_start = 0
        for j, line in enumerate(lines):
            if line.strip() == "":
                code_start = j + 1
                break
        code_lines = lines[code_start:]
        output.append("\n".join(code_lines[:60]))  # max 60 lines per result
        if len(code_lines) > 60:
            output.append(f"  ... ({len(code_lines)} lines total)")
        output.append("")

    return truncate_output("\n".join(output))


@tool(args_schema=IndexCodebaseArgs)
def index_codebase(force: bool = False) -> str:
    """Index or re-index the workspace codebase for semantic search.

    This scans all code files, splits them into chunks, generates embeddings
    via Ollama, and stores them in Milvus vector database.

    Only new or modified files are indexed (unless force=True).

    Args:
        force: If True, re-index all files even if unchanged. Default False.

    Returns:
        Indexing statistics.
    """
    indexer = get_indexer()

    # Pre-check: verify Ollama is reachable
    import requests
    try:
        resp = requests.get(
            f"{getattr(__import__('config'), 'OLLAMA_BASE_URL', 'http://localhost:11434')}/api/tags",
            timeout=5,
        )
        if resp.status_code != 200:
            return "❌ Cannot reach Ollama server. Make sure `ollama serve` is running."
    except Exception:
        return "❌ Cannot connect to Ollama. Make sure `ollama serve` is running."

    stats = indexer.index_workspace(force=force)

    output = [
        "📊 Indexing complete!",
        f"  ✅ Indexed: {stats['indexed']} files",
        f"  ⏭️ Skipped (unchanged): {stats['skipped']} files",
        f"  ❌ Errors: {stats['errors']}",
        f"  📦 Total chunks in DB: {stats['total_chunks']}",
        f"  📁 Workspace: {indexer.workspace}",
        f"  🧠 Embedding: {indexer.embedding_model}",
    ]
    return "\n".join(output)
