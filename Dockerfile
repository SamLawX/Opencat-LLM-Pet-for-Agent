# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY backend/requirements.txt ./

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the backend files into the container at /app
COPY backend/ ./

# Expose port 8000
EXPOSE 8000

# Define environment variables (can be overridden)
ENV TICK_INTERVAL=30
ENV PORT=8000
ENV HOST=0.0.0.0

# Run the backend when the container launches
CMD ["sh", "-c", "python main.py --host $HOST --port $PORT --tick-interval $TICK_INTERVAL"]
