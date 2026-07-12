FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /hypernova
COPY pyproject.toml README.md ./
COPY hypernova ./hypernova
RUN pip install --no-cache-dir .
ENTRYPOINT ["hypernova"]
