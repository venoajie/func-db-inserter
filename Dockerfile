
# Stage 1: Builder - where we install dependencies
FROM python:3.12.3-slim-bookworm as builder

WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Stage 2: Runtime - the final, lean image
FROM python:3.12.3-slim-bookworm

WORKDIR /app

# Copy installed dependencies from the builder stage
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy the application code
COPY main.py .

# This command launches uvicorn and explicitly tells it to listen
# on the Unix Domain Socket provided by the OCI Functions platform. This is the
# definitive fix for 504 timeout errors.
CMD [ "uvicorn", "main:app", "--uds", "/tmp/iofs/lsnr.sock" ]
