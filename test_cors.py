import requests

try:
    print("Testing OPTIONS request to http://localhost:8000/api/model")
    resp = requests.options(
        "http://localhost:8000/api/model",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        }
    )
    print(f"Status: {resp.status_code}")
    print("Headers:", resp.headers)
    
    print("\nTesting GET request to http://localhost:8000/api/model")
    resp = requests.get(
        "http://localhost:8000/api/model",
        headers={
            "Origin": "http://localhost:3000",
        }
    )
    print(f"Status: {resp.status_code}")
    print("Headers:", resp.headers)
except Exception as e:
    print(f"Error: {e}")
