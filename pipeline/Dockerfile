FROM ghcr.io/open-webui/pipelines:main

# Install pipeline-specific dependencies
COPY requirements.txt /tmp/pipeline_requirements.txt
RUN pip install --no-cache-dir -r /tmp/pipeline_requirements.txt

# Copy the ContextRealm memory pipeline
COPY mem0_filter.py /app/pipelines/mem0_filter.py
