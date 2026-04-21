FROM python:3.11-slim

LABEL maintainer="devops@example.com"
LABEL version="1.0"

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV APP_PORT=8080

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["python", "main.py"]
