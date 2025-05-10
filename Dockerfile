FROM python:3.12-alpine

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -r requirements.txt 

EXPOSE 8545 8546
ENTRYPOINT ["python3", "-m", "gear.cli","--host", "0.0.0.0", "--endpoint"]
