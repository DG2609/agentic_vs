from duckduckgo_search import DDGS
print("Testing DDGS...")
try:
    with DDGS() as ddgs:
        queries = [("thời tiết hôm nay", "api"), ("thời tiết hôm nay", "html"), ("thời tiết hôm nay", "lite")]
        for q, backend in queries:
            print(f"--- Query: {q} (Backend: {backend}) ---")
            try:
                results = list(ddgs.text(q, backend=backend, max_results=2))
                print(f"Count: {len(results)}")
                if results:
                    print(f"First: {results[0].get('title')}")
            except Exception as e:
                print(f"Error: {e}")
except Exception as e:
    print(f"Error: {e}")
