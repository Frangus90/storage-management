# app.py - Main Flask Application
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime
import os
import uuid

app = Flask(__name__)
CORS(app)

# Database Configuration for Supabase
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    # Handle both postgres:// and postgresql:// prefixes
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
    elif DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL or 'sqlite:///storage.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Database Models
class Plate(db.Model):
    __tablename__ = 'plates'
    
    id = db.Column(db.Integer, primary_key=True)
    size = db.Column(db.String(20), nullable=False, unique=True)
    quantity = db.Column(db.Integer, nullable=False, default=0)
    threshold = db.Column(db.Integer, nullable=False, default=50)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'size': self.size,
            'quantity': self.quantity,
            'threshold': self.threshold,
            'status': 'low' if self.quantity <= self.threshold else 'ok'
        }

class InboundQueue(db.Model):
    __tablename__ = 'inbound_queue'
    
    id = db.Column(db.Integer, primary_key=True)
    plate_size = db.Column(db.String(20), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    batch_id = db.Column(db.String(50), nullable=False, unique=True)
    status = db.Column(db.String(20), nullable=False, default='pending')
    boxes = db.Column(db.Integer, nullable=True)
    plates_per_box = db.Column(db.Integer, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'plate_size': self.plate_size,
            'quantity': self.quantity,
            'batch_id': self.batch_id,
            'status': self.status,
            'boxes': self.boxes,
            'plates_per_box': self.plates_per_box,
            'timestamp': self.timestamp.isoformat()
        }

class Transaction(db.Model):
    __tablename__ = 'transactions'
    
    id = db.Column(db.Integer, primary_key=True)
    plate_size = db.Column(db.String(20), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    type = db.Column(db.String(10), nullable=False)  # 'in' or 'out'
    source = db.Column(db.String(20), nullable=False)  # 'qr' or 'manual'
    batch_id = db.Column(db.String(50), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'plate_size': self.plate_size,
            'quantity': self.quantity,
            'type': self.type,
            'source': self.source,
            'batch_id': self.batch_id,
            'notes': self.notes,
            'date': self.date.isoformat()
        }

# API Routes
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/plates')
def get_plates():
    plates = Plate.query.all()
    return jsonify([plate.to_dict() for plate in plates])

@app.route('/api/plates', methods=['POST'])
def update_plate():
    data = request.json
    plate = Plate.query.get(data['id'])
    if plate:
        plate.quantity = data['quantity']
        plate.threshold = data['threshold']
        db.session.commit()
        return jsonify(plate.to_dict())
    return jsonify({'error': 'Plate not found'}), 404

@app.route('/api/inbound', methods=['POST'])
def process_qr():
    data = request.json
    qr_data = data.get('qr_data', '').strip()
    
    try:
        # Parse pipe-separated format: plate|boxes|plates_per_box|pallet_id
        if '|' in qr_data:
            parts = qr_data.split('|')
            if len(parts) != 4:
                return jsonify({'error': 'Invalid pallet format'}), 400
            
            plate_size = parts[0].strip()
            boxes = int(parts[1])
            plates_per_box = int(parts[2])
            pallet_id = parts[3].strip()
            total_qty = boxes * plates_per_box
            
            # Check for duplicate pallet ID
            existing = InboundQueue.query.filter_by(batch_id=pallet_id).first()
            if existing:
                return jsonify({'error': 'Pallet ID already exists'}), 400
            
            # Create new inbound entry
            inbound = InboundQueue(
                plate_size=plate_size,
                quantity=total_qty,
                batch_id=pallet_id,
                boxes=boxes,
                plates_per_box=plates_per_box
            )
            
        else:
            # Legacy URL format support
            return jsonify({'error': 'Legacy URL format not supported in this version'}), 400
        
        db.session.add(inbound)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'pallet': inbound.to_dict()
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/pending')
def get_pending():
    pending = InboundQueue.query.filter_by(status='pending').all()
    return jsonify([item.to_dict() for item in pending])

@app.route('/api/approve/<batch_id>', methods=['POST'])
def approve_delivery(batch_id):
    inbound = InboundQueue.query.filter_by(batch_id=batch_id, status='pending').first()
    if not inbound:
        return jsonify({'error': 'Pending delivery not found'}), 404
    
    # Update plate stock
    plate = Plate.query.filter_by(size=inbound.plate_size).first()
    if plate:
        plate.quantity += inbound.quantity
    
    # Mark as approved
    inbound.status = 'approved'
    
    # Add transaction
    transaction = Transaction(
        plate_size=inbound.plate_size,
        quantity=inbound.quantity,
        type='in',
        source='qr',
        batch_id=batch_id
    )
    
    db.session.add(transaction)
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/api/reject/<batch_id>', methods=['POST'])
def reject_delivery(batch_id):
    inbound = InboundQueue.query.filter_by(batch_id=batch_id, status='pending').first()
    if not inbound:
        return jsonify({'error': 'Pending delivery not found'}), 404
    
    inbound.status = 'rejected'
    db.session.commit()
    
    return jsonify({'success': True})

@app.route('/api/manual', methods=['POST'])
def manual_adjustment():
    data = request.json
    
    plate = Plate.query.filter_by(size=data['plate_size']).first()
    if not plate:
        return jsonify({'error': 'Plate not found'}), 404
    
    quantity = int(data['quantity'])
    adj_type = data['type']
    
    # Check stock availability for removals
    if adj_type == 'out' and plate.quantity < quantity:
        return jsonify({'error': 'Not enough stock available'}), 400
    
    # Update stock
    if adj_type == 'in':
        plate.quantity += quantity
    else:
        plate.quantity -= quantity
    
    # Add transaction
    transaction = Transaction(
        plate_size=data['plate_size'],
        quantity=quantity,
        type=adj_type,
        source='manual',
        notes=data.get('notes', '')
    )
    
    db.session.add(transaction)
    db.session.commit()
    
    return jsonify({'success': True, 'plate': plate.to_dict()})

@app.route('/api/transactions')
def get_transactions():
    transactions = Transaction.query.order_by(Transaction.date.desc()).limit(100).all()
    return jsonify([t.to_dict() for t in transactions])

@app.route('/api/stats')
def get_stats():
    total_plates = Plate.query.count()
    low_stock = Plate.query.filter(Plate.quantity <= Plate.threshold).count()
    pending_deliveries = InboundQueue.query.filter_by(status='pending').count()
    
    return jsonify({
        'total_plates': total_plates,
        'low_stock': low_stock,
        'pending_deliveries': pending_deliveries
    })

@app.route('/api/generate-pallet-id')
def generate_pallet_id():
    timestamp = str(int(datetime.now().timestamp()))[-6:]
    random_num = str(uuid.uuid4().int)[:3]
    pallet_id = f"PLT{timestamp}{random_num}"
    return jsonify({'pallet_id': pallet_id})

# Initialize database
def init_db():
    with app.app_context():
        db.create_all()
        
        # Check if we need to seed data
        if Plate.query.count() == 0:
            # Create sample plates
            plate_sizes = [
                '50x100', '75x150', '100x200', '50x150', '75x100', '100x150',
                '50x200', '75x200', '100x100', '125x150', '125x200', '150x200',
                '50x250', '75x250', '100x250', '125x250', '150x250', '200x250',
                '50x300', '75x300', '100x300', '125x300', '150x300', '200x300',
                '62x150', '87x200', '112x250', '137x300', '162x350', '187x400',
                '38x125', '63x175', '88x225', '113x275', '138x325', '163x375',
                '44x140', '69x190', '94x240'
            ]
            
            import random
            for size in plate_sizes:
                plate = Plate(
                    size=size,
                    quantity=random.randint(50, 450),
                    threshold=random.randint(50, 149)
                )
                db.session.add(plate)
            
            # Add sample pending delivery
            sample_inbound = InboundQueue(
                plate_size='75x150',
                quantity=500,
                batch_id='PLT123456789',
                boxes=20,
                plates_per_box=25
            )
            db.session.add(sample_inbound)
            
            db.session.commit()
            print("Database initialized with sample data!")

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
