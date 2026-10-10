FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt requirements-azure.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-azure.txt

COPY *.py ./

CMD ["python", "azure_run.py"]
