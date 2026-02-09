# DCIM Prediction Service

AI-powered prediction service for DCIM metrics forecasting.

## Features

- Time-series forecasting for CPU, memory, disk, and temperature metrics
- Anomaly detection with z-score analysis
- RESTful API with CORS support
- Simple deployment with Flask or Gunicorn

## Installation

```bash
cd prediction_service
pip install -r requirements.txt
```

## Running the Service

### Development
```bash
python app.py
```

### Production
```bash
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

## API Endpoints

### POST /api/predict
Predict future metric values.

**Request:**
```json
{
  "metric_type": "cpu.usage",
  "history": [
    {"timestamp": "2025-01-01T00:00:00Z", "value": 45.2},
    {"timestamp": "2025-01-02T00:00:00Z", "value": 48.1}
  ],
  "forecast_days": 7
}
```

**Response:**
```json
{
  "predictions": [
    {
      "ds": "2025-01-03T00:00:00Z",
      "yhat": 50.5,
      "yhat_lower": 45.0,
      "yhat_upper": 56.0
    }
  ],
  "model": "Simple Forecast",
  "metric_type": "cpu.usage"
}
```

### POST /api/anomaly-score
Calculate anomaly scores for values.

**Request:**
```json
{
  "values": [45.2, 48.1, 50.3, 89.5, 52.1]
}
```

**Response:**
```json
{
  "scores": [
    {
      "value": 45.2,
      "z_score": -0.85,
      "is_anomaly": false
    },
    {
      "value": 89.5,
      "z_score": 2.67,
      "is_anomaly": true
    }
  ]
}
```

## Production Deployment

For production, consider using:
- **Prophet** for advanced time-series forecasting
- **ARIMA/SARIMA** for seasonal patterns
- **Redis** for caching predictions
- **Docker** for containerization

## Notes

- The current implementation uses a simple forecasting algorithm
- For production use, uncomment Prophet in requirements.txt and implement Prophet-based forecasting
- The service expects numeric values between 0-100 for most metrics
