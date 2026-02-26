FROM python:3.10-slim

# Prevent Python from buffering logs (important for Render logs)
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system deps (optional but safe)
RUN apt-get update && apt-get install -y \
    gcc \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy files
COPY . .

# Install Python dependencies
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Expose port (Render uses 10000)
EXPOSE 10000

# Run app
CMD ["python", "app.py"]
