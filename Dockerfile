FROM node:20-slim AS frontend-build

WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Expose API port
EXPOSE 8000

# Initialize hosted databases when needed, then run API
CMD ["sh", "-c", "python -m scripts.bootstrap_hosted && python -m api.run"]
