FROM python:3.11-slim
# Use /app inside the container. Install dependencies before copying source so
# Docker can reuse that installation layer when only application code changes.
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd --create-home appuser
# Run the backend as an ordinary user rather than the container's root user.
USER appuser
EXPOSE 8000
# app.main:app selects the FastAPI object. Binding 0.0.0.0 allows requests from
# outside the container; Compose maps the port to the host computer.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
