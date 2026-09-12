FROM python:3.12-slim

WORKDIR /app

# Install ONLY the necessary production dependencies
RUN pip install kubernetes google-genai

# Copy your actual code into the container
COPY single_agent.py .
RUN mkdir -p reports

# When the container boots, run the whole thing
CMD ["python", "single_agent.py"]