FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create output and tmp directories
RUN mkdir -p output tmp

# Expose the FastAPI port
EXPOSE 8000

# Run with single worker — pipeline uses in-memory progress tracking
# that cannot be shared across worker processes
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
