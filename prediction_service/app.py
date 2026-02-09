from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'service': 'DCIM Prediction Service'})

@app.route('/api/predict', methods=['POST'])
def predict():
    """
    Predict future metric values using time series forecasting.
    Expects JSON with:
    - metric_type: string
    - history: array of {timestamp, value}
    - forecast_days: number (default 7)
    """
    try:
        data = request.json
        metric_type = data.get('metric_type')
        history = data.get('history', [])
        forecast_days = data.get('forecast_days', 7)

        if not history:
            return jsonify({'error': 'No history data provided'}), 400

        # Convert to DataFrame
        df = pd.DataFrame(history)
        df['ds'] = pd.to_datetime(df['timestamp'])
        df['y'] = df['value'].astype(float)
        df = df[['ds', 'y']].sort_values('ds')

        # Simple forecasting using moving average and linear trend
        # In production, use Prophet or ARIMA for better accuracy
        predictions = simple_forecast(df, forecast_days)

        return jsonify({
            'predictions': predictions,
            'model': 'Simple Forecast',
            'metric_type': metric_type
        })

    except Exception as e:
        logger.error(f'Prediction error: {str(e)}')
        return jsonify({'error': str(e)}), 500

def simple_forecast(df, forecast_days):
    """
    Simple forecasting using moving average and linear trend.
    For production, replace with Prophet, ARIMA, or LSTM.
    """
    # Calculate trend
    df['index'] = range(len(df))
    trend_coef = np.polyfit(df['index'], df['y'], 1)

    # Calculate moving average
    window = min(7, len(df))
    df['ma'] = df['y'].rolling(window=window).mean()

    # Calculate standard deviation for confidence bands
    std_dev = df['y'].std()

    # Generate predictions
    last_date = df['ds'].max()
    last_value = df['y'].iloc[-1]
    last_ma = df['ma'].iloc[-1] if not pd.isna(df['ma'].iloc[-1]) else last_value

    predictions = []
    for i in range(1, forecast_days + 1):
        # Linear extrapolation with moving average
        predicted_value = last_ma + (trend_coef[0] * i)

        # Ensure value stays within reasonable bounds
        predicted_value = max(0, min(100, predicted_value))  # Clamp between 0-100

        # Calculate confidence intervals
        lower_bound = max(0, predicted_value - (1.96 * std_dev))
        upper_bound = min(100, predicted_value + (1.96 * std_dev))

        pred_date = last_date + timedelta(days=i)

        predictions.append({
            'ds': pred_date.isoformat(),
            'yhat': float(predicted_value),
            'yhat_lower': float(lower_bound),
            'yhat_upper': float(upper_bound)
        })

    return predictions

@app.route('/api/anomaly-score', methods=['POST'])
def anomaly_score():
    """
    Calculate anomaly scores for given values.
    Expects JSON with:
    - values: array of numbers
    """
    try:
        data = request.json
        values = data.get('values', [])

        if not values:
            return jsonify({'error': 'No values provided'}), 400

        # Calculate z-scores
        values_array = np.array(values)
        mean = np.mean(values_array)
        std = np.std(values_array)

        if std == 0:
            z_scores = np.zeros(len(values_array))
        else:
            z_scores = (values_array - mean) / std

        scores = [
            {
                'value': float(val),
                'z_score': float(z),
                'is_anomaly': abs(z) > 2.5  # 2.5 sigma threshold
            }
            for val, z in zip(values, z_scores)
        ]

        return jsonify({'scores': scores})

    except Exception as e:
        logger.error(f'Anomaly detection error: {str(e)}')
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    logger.info('Starting DCIM Prediction Service on port 5000...')
    app.run(host='0.0.0.0', port=5000, debug=True)
