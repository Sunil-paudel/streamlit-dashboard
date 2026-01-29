# Production Deployment Guide: Redis

When moving your Moodle Analytics Dashboard to production (e.g., Heroku, Render, AWS, or a VPS), you need a reliable Redis instance.

## 🚀 Options for Production Redis

### 1. Managed Services (Recommended)
These are easiest to set up and usually have a "Free Tier":
- **Upstash**: Serverless Redis with a very generous free tier. Great for Streamlit apps.
- **Redis Cloud**: Managed Redis service from the creators of Redis.
- **Heroku Data for Redis**: If you are deploying to Heroku, this is just a one-click add-on.

### 2. Manual Setup (VPS / Docker)
If you are running your own server:
- **Docker**: `docker run -d --name redis -p 6379:6379 redis`
- **Linux**: `sudo apt install redis-server`

---

## ⚙️ Configuration in Production

In production, you should use the `REDIS_URL` environment variable. This is the "Industry Standard" way to connect and is much simpler than managing Host/Port separately.

**Format:**
`redis://username:password@host:port/db`

### Examples:
- **Heroku**: Automatically provides a `REDIS_URL` variable for you. Our app will detect it automatically.
- **Render/Other**: Copy the "Connection String" from your Redis provider and add it as an environment variable named `REDIS_URL`.

### Connection Security (TLS/SSL)
If your Redis provider requires a secure connection (common in production), use `rediss://` (two 's's) instead of `redis://`. 
The app is now configured to handle these secure connections automatically.

---

## 💡 Summary of Changes Made
1.  **requirements.txt**: Added `redis` so it installs automatically on your production server.
2.  **redis_client.py**: Added support for `REDIS_URL` which simplifies configuration.
3.  **Security**: Added support for `rediss://` (SSL) connections.
