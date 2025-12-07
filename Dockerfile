# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set environment variables
# PYTHONDONTWRITEBYTECODE: Prevents Python from writing pyc files to disc (equivalent to python -B option)
# PYTHONUNBUFFERED: Prevents Python from buffering stdout and stderr (equivalent to python -u option)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Set the working directory in the container
WORKDIR /app

# Install system dependencies (if any required for dependencies like cryptography)
# build-essential and libssl-dev might be needed for some python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libssl-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container at /app
COPY requirements.txt /app/

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the current directory contents into the container at /app/gabs_api_server
# We copy to a subdirectory to maintain the package structure
COPY . /app/gabs_api_server/

# Create a non-root user for security
RUN useradd -m gabsuser && \
    mkdir /data && \
    chown -R gabsuser:gabsuser /app /data

USER gabsuser

# Make port 5000 available to the world outside this container
EXPOSE 5000

# Default command to run the web server
# Can be overridden in docker-compose to run the scheduler
CMD ["python", "-m", "gabs_api_server.app"]

