"""Test 1: Read bottom of file + Test 3: Fix bug."""
import asyncio
import json
import websockets


async def run_test(name, message, thread_id, timeout=300):
    uri = "ws://localhost:8000/ws/chat"
    async with websockets.connect(uri, ping_timeout=timeout) as ws:
        await ws.send(json.dumps({"message": message, "thread_id": thread_id}))
        text = ""
        tools = []
        title = ""
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            data = json.loads(raw)
            t = data["type"]
            if t == "text":
                text += data.get("content", "")
            elif t == "tool_start":
                tools.append(data["tool"]["tool_name"])
            elif t == "title":
                title = data.get("content", "")
            elif t == "done":
                break
    return {"name": name, "text": text, "tools": tools, "title": title}


async def main():
    # TEST 1: Read bottom of 600-line file
    print("Running Test 1: Read bottom of file...")
    r1 = await run_test(
        "Read Bottom",
        "Read ecommerce_system.py and tell me: what methods does the AnalyticsEngine class have? "
        "List each method name and what it returns.",
        "test-read-bottom-1",
    )

    with open("test_read_result.txt", "w", encoding="utf-8") as f:
        f.write(f"Tools: {r1['tools']}\n")
        f.write(f"Title: {r1['title']}\n\n")
        f.write(r1["text"])
    print(f"  Done! Tools: {r1['tools']}, {len(r1['text'])} chars")
    print(f"  Saved to test_read_result.txt")

    # TEST 3: Fix bug
    print("\nRunning Test 3: Fix bug...")
    r3 = await run_test(
        "Fix Bug",
        "In ecommerce_system.py, the OrderItem.validate() method has a bug in the discount "
        "validation: the check `self.discount_amount > self.subtotal + self.discount_amount` "
        "is always false. Fix this bug using file_edit.",
        "test-fix-bug-1",
    )

    with open("test_fix_result.txt", "w", encoding="utf-8") as f:
        f.write(f"Tools: {r3['tools']}\n")
        f.write(f"Title: {r3['title']}\n\n")
        f.write(r3["text"])
    print(f"  Done! Tools: {r3['tools']}, {len(r3['text'])} chars")
    print(f"  Saved to test_fix_result.txt")


if __name__ == "__main__":
    asyncio.run(main())
