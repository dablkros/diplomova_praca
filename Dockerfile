FROM python:3.11-slim

# Nastav pracovný adresár
WORKDIR /app

# Skopíruj requirements a zdrojáky
COPY requirements.txt .
COPY . .

# Nainštaluj závislosti
RUN pip install --no-cache-dir -r requirements.txt

# Spusti FastAPI cez uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
