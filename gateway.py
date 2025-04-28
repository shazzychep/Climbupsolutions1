from flask import Flask, request, jsonify
from flask_jwt_extended import JWTManager, jwt_required, get_jwt_identity
from datetime import datetime, timedelta
import redis
from ..services.rule_engine.scheduling_rules import SchedulingRuleEngine
from ..models.mongodb.rules import RuleEngine
from ..models.postgresql.models import db, Appointment, SlotHold, User

app = Flask(__name__)
app.config['JWT_SECRET_KEY'] = 'your-secret-key'  # Move to config
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://user:pass@localhost/climbup'
app.config['REDIS_URL'] = 'redis://localhost:6379/0'

jwt = JWTManager(app)
redis_client = redis.from_url(app.config['REDIS_URL'])

# Initialize rule engines
rule_engine = RuleEngine('mongodb://localhost:27017')
scheduling_engine = SchedulingRuleEngine(rule_engine)

@app.route('/api/availability', methods=['GET'])
@jwt_required()
def get_availability():
    """Get available slots for a consultant"""
    consultant_id = request.args.get('consultant_id', type=int)
    date = datetime.strptime(request.args.get('date'), '%Y-%m-%d')
    duration = request.args.get('duration', 60, type=int)  # Default 60 minutes
    
    slots = scheduling_engine.get_available_slots(consultant_id, date, duration)
    return jsonify({'slots': slots})

@app.route('/api/book', methods=['POST'])
@jwt_required()
def book_appointment():
    """Book an appointment with slot hold"""
    data = request.get_json()
    client_id = get_jwt_identity()
    
    # Create slot hold
    slot_hold = SlotHold(
        client_id=client_id,
        consultant_id=data['consultant_id'],
        start_time=datetime.fromisoformat(data['start_time']),
        end_time=datetime.fromisoformat(data['end_time']),
        status='active',
        expires_at=datetime.utcnow() + timedelta(seconds=900)  # 15 minutes
    )
    
    db.session.add(slot_hold)
    db.session.commit()
    
    # Store in Redis for quick access
    redis_key = f"slot_hold:{slot_hold.id}"
    redis_client.setex(redis_key, 900, slot_hold.to_json())
    
    return jsonify({
        'slot_hold_id': slot_hold.id,
        'expires_at': slot_hold.expires_at.isoformat()
    })

@app.route('/api/confirm-booking', methods=['POST'])
@jwt_required()
def confirm_booking():
    """Confirm booking and create appointment"""
    data = request.get_json()
    client_id = get_jwt_identity()
    
    # Verify slot hold
    slot_hold = SlotHold.query.get(data['slot_hold_id'])
    if not slot_hold or not scheduling_engine.validate_slot_hold(slot_hold):
        return jsonify({'error': 'Slot hold expired or invalid'}), 400
    
    # Create appointment
    appointment = Appointment(
        client_id=client_id,
        consultant_id=slot_hold.consultant_id,
        start_time=slot_hold.start_time,
        end_time=slot_hold.end_time,
        status='pending',
        payment_status='pending'
    )
    
    db.session.add(appointment)
    slot_hold.status = 'converted'
    db.session.commit()
    
    return jsonify({'appointment_id': appointment.id})

@app.route('/api/verify-payment', methods=['POST'])
@jwt_required()
def verify_payment():
    """Webhook endpoint for payment verification"""
    data = request.get_json()
    appointment_id = data['appointment_id']
    payment_proof = data['payment_proof_url']
    
    appointment = Appointment.query.get(appointment_id)
    if not appointment:
        return jsonify({'error': 'Appointment not found'}), 404
    
    # Update appointment with payment proof
    appointment.payment_proof_url = payment_proof
    appointment.payment_status = 'verified'
    appointment.status = 'confirmed'
    
    db.session.commit()
    
    # Trigger notifications
    # TODO: Implement notification service
    
    return jsonify({'status': 'success'})

@app.route('/api/consultants', methods=['GET'])
@jwt_required()
def get_consultants():
    """Get available consultants with optional filters"""
    specialization = request.args.get('specialization')
    preferred_only = request.args.get('preferred_only', False, type=bool)
    
    consultants = scheduling_engine.match_consultant(
        specialization,
        preferred_only
    )
    
    return jsonify({
        'consultants': [c.to_dict() for c in consultants]
    })

if __name__ == '__main__':
    app.run(debug=True) 