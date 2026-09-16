FROM python:3.11-slim

WORKDIR /app

# Build dependencies for psycopg2. No image processing happens in this
# container — uploads go to object storage as they arrive — so there is
# nothing else to install.
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 8000

# Run the application.
# --proxy-headers/--forwarded-allow-ips let uvicorn trust the ingress's
# X-Forwarded-Proto and X-Forwarded-For. Without them it sees plain http and
# builds its trailing-slash redirects as http:// URLs, which a browser on the
# https site blocks as mixed content — and every anonymous commenter arrives
# wearing the ingress's address, which would make one rate limit for all of
# them.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
