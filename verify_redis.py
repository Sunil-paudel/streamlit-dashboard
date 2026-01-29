import sys
import os
# Add current directory to path
sys.path.append(os.getcwd())

from redis_client import get_redis, PREFIX_CACHE

def test_redis():
    redis = get_redis()
    if not redis.client:
        print("FAIL: Redis client not connected")
        return

    test_key = f"{PREFIX_CACHE}test_connection"
    test_data = {"status": "ok", "message": "Redis is working!"}
    
    print(f"Setting JSON to {test_key}...")
    redis.set_json(test_key, test_data, ex=10)
    
    print("Retrieving JSON...")
    retrieved = redis.get_json(test_key)
    
    if retrieved == test_data:
        print("SUCCESS: Redis connection and JSON serialization verified!")
    else:
        print(f"FAIL: Data mismatch. Expected {test_data}, got {retrieved}")

    redis.delete(test_key)

if __name__ == "__main__":
    test_redis()
