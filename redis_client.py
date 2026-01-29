import redis
import json
import os
from dotenv import load_dotenv

load_dotenv()

class RedisClient:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(RedisClient, cls).__new__(cls)
            
            # Check for REDIS_URL first (Standard for production)
            redis_url = os.getenv("REDIS_URL")
            
            try:
                if redis_url:
                    # Support SSL connections common in production (rediss://)
                    cls._instance.client = redis.Redis.from_url(
                        redis_url, 
                        decode_responses=True,
                        # Many managed redis services use self-signed certs
                        ssl_cert_reqs=None 
                    )
                else:
                    host = os.getenv("REDIS_HOST", "localhost")
                    port = int(os.getenv("REDIS_PORT", 6379))
                    db = int(os.getenv("REDIS_DB", 0))
                    cls._instance.client = redis.Redis(host=host, port=port, db=db, decode_responses=True)
            except Exception as e:
                print(f"Error connecting to Redis: {e}")
                cls._instance.client = None
        return cls._instance

    def set_json(self, key, data, ex=None):
        """Serializes data to JSON and stores it in Redis."""
        if not self.client: return False
        try:
            val = json.dumps(data)
            return self.client.set(key, val, ex=ex)
        except Exception as e:
            print(f"Redis set_json error: {e}")
            return False

    def get_json(self, key):
        """Retrieves and deserializes JSON data from Redis."""
        if not self.client: return None
        try:
            val = self.client.get(key)
            return json.loads(val) if val else None
        except Exception as e:
            print(f"Redis get_json error: {e}")
            return None

    def delete(self, key):
        """Deletes a key from Redis."""
        if not self.client: return False
        try:
            return self.client.delete(key)
        except Exception as e:
            print(f"Redis delete error: {e}")
            return False

    def keys(self, pattern):
        """Finds keys matching a pattern."""
        if not self.client: return []
        try:
            return self.client.keys(pattern)
        except Exception as e:
            print(f"Redis keys error: {e}")
            return []

# Prefixes for keys to avoid collisions
PREFIX_CACHE = "moodle_dash:cache:"
PREFIX_DRAFT = "moodle_dash:draft:"

def get_redis():
    return RedisClient()
