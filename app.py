from flask import Flask, jsonify
from flask_cors import CORS
from flask_limiter import Limiter
from flask_jwt_extended import JWTManager
from datetime import timedelta
import logging
from logging.handlers import RotatingFileHandler
import os
from flask_limiter.util import get_remote_address
import redis
import time
from flask_sqlalchemy import SQLAlchemy
from pymongo import MongoClient

from routes.auth import auth_bp
from routes.booking import booking_bp
from routes.admin import admin_bp
from routes.consultant import consultant_bp
from routes.payment import payment_bp
from routes.availability import availability_bp

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler('logs/app.log', maxBytes=10000000, backupCount=5),
        logging.StreamHandler()
    ]
)

# Initialize Redis connection
redis_client = redis.Redis(
    host=os.getenv('REDIS_HOST', 'localhost'),
    port=int(os.getenv('REDIS_PORT', 6379)),
    db=0
)

# Initialize MongoDB connection
mongo_client = MongoClient(os.getenv('MONGODB_URI', 'mongodb://localhost:27017/climbup'))
mongo_db = mongo_client.get_database()

app = Flask(__name__)
CORS(app)

# Configuration
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'your-secret-key')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=1)
app.config['JWT_REFRESH_TOKEN_EXPIRES'] = timedelta(days=30)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'postgresql://user:password@localhost/climbup')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MONGODB_URI'] = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/climbup')
app.config['RATELIMIT_STORAGE_URL'] = os.getenv('REDIS_URL', 'redis://localhost:6379/0')

# Initialize extensions
jwt = JWTManager(app)
db = SQLAlchemy(app)
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    storage_uri=app.config['RATELIMIT_STORAGE_URL']
)

# Register blueprints
app.register_blueprint(auth_bp, url_prefix='/api/auth', decorators=[limiter.limit("50 per minute")])
app.register_blueprint(booking_bp, url_prefix='/api/booking', decorators=[limiter.limit("100 per minute")])
app.register_blueprint(admin_bp, url_prefix='/api/admin', decorators=[limiter.limit("100 per minute")])
app.register_blueprint(consultant_bp, url_prefix='/api/consultant', decorators=[limiter.limit("100 per minute")])
app.register_blueprint(payment_bp, url_prefix='/api/payment', decorators=[limiter.limit("100 per minute")])
app.register_blueprint(availability_bp, url_prefix='/api/availability', decorators=[limiter.limit("100 per minute")])

@app.route('/health', methods=['GET'])
def health_check():
    health_status = {
        'status': 'healthy',
        'timestamp': time.time(),
        'services': {}
    }
    
    # Test PostgreSQL connection
    postgres_start = time.time()
    try:
        db.engine.execute('SELECT 1')
        postgres_latency = (time.time() - postgres_start) * 1000  # Convert to milliseconds
        health_status['services']['postgresql'] = {
            'status': 'healthy',
            'latency_ms': round(postgres_latency, 2)
        }
    except Exception as e:
        health_status['services']['postgresql'] = {
            'status': 'unhealthy',
            'error': str(e),
            'latency_ms': round((time.time() - postgres_start) * 1000, 2)
        }
        health_status['status'] = 'degraded'
    
    # Test MongoDB connection
    mongo_start = time.time()
    try:
        mongo_db.command('ping')
        mongo_latency = (time.time() - mongo_start) * 1000
        health_status['services']['mongodb'] = {
            'status': 'healthy',
            'latency_ms': round(mongo_latency, 2)
        }
    except Exception as e:
        health_status['services']['mongodb'] = {
            'status': 'unhealthy',
            'error': str(e),
            'latency_ms': round((time.time() - mongo_start) * 1000, 2)
        }
        health_status['status'] = 'degraded'
    
    # Test Redis connection
    redis_start = time.time()
    try:
        redis_client.ping()
        redis_latency = (time.time() - redis_start) * 1000
        health_status['services']['redis'] = {
            'status': 'healthy',
            'latency_ms': round(redis_latency, 2)
        }
    except Exception as e:
        health_status['services']['redis'] = {
            'status': 'unhealthy',
            'error': str(e),
            'latency_ms': round((time.time() - redis_start) * 1000, 2)
        }
        health_status['status'] = 'degraded'
    
    # Determine overall status code
    status_code = 200
    if health_status['status'] == 'degraded':
        status_code = 503
    
    return jsonify(health_status), status_code

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({
        'error': 'Too many requests',
        'message': 'Rate limit exceeded. Please try again later.',
        'retry_after': e.description
    }), 429

if __name__ == '__main__':
    app.run(debug=True) 