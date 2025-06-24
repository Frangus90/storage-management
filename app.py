# app.py - Main Flask Application
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime
import os
import uuid
import re

app = Flask(__name__)
CORS(app)

# Database Configuration for Supabase
DATABASE_URL = os.environ.get('DATABASE_URL')
print(f"Original DATABASE_URL: {DATABASE_URL}")

if DATABASE_URL:
    # Handle both postgres:// and postgresql:// prefixes
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
    elif DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
    print(f"Transformed DATABASE_URL: {DATABASE_URL}")

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL or 'sqlite:///storage.db'
print(f"Final SQLALCHEMY_DATABASE_URI: {app.config['SQLALCHEMY_DATABASE_URI']}")
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
    try:
        plates = Plate.query.all()
        print(f"API /api/plates - Found {len(plates)} plates in database")
        if plates:
            print(f"First plate example: {plates[0].size} = {plates[0].quantity}")
        result = [plate.to_dict() for plate in plates]
        return jsonify(result)
    except Exception as e:
        print(f"Error in /api/plates: {e}")
        return jsonify({'error': str(e)}), 500

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
            
            try:
                boxes = int(parts[1])
                plates_per_box = int(parts[2])
            except ValueError:
                return jsonify({'error': 'PQ (boxes) and BQ (plates per box) must be numbers'}), 400
            
            pallet_id = parts[3].strip()
            
            if not pallet_id:
                return jsonify({'error': 'UQID (pallet ID) cannot be empty'}), 400
            
            if boxes <= 0 or plates_per_box <= 0:
                return jsonify({'error': 'PQ and BQ must be positive numbers'}), 400
            
            total_qty = boxes * plates_per_box
            
            # Validate plate size format
            if not re.match(r'^\d+x\d+

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
    else:
        # Create new plate if it doesn't exist
        plate = Plate(
            size=inbound.plate_size,
            quantity=inbound.quantity,
            threshold=50  # Default threshold
        )
        db.session.add(plate)
    
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

@app.route('/api/plates/new', methods=['POST'])
def add_new_plate():
    data = request.json
    
    # Validate input
    plate_size = data.get('plate_size', '').strip()
    if not plate_size:
        return jsonify({'error': 'Plate size is required'}), 400
    
    # Validate format (e.g., 100x200)
    if not re.match(r'^\d+x\d+$', plate_size):
        return jsonify({'error': 'Invalid plate size format. Use format: WIDTHxHEIGHT (e.g., 100x200)'}), 400
    
    # Check if plate already exists
    existing = Plate.query.filter_by(size=plate_size).first()
    if existing:
        return jsonify({'error': f'Plate size {plate_size} already exists'}), 400
    
    # Create new plate
    try:
        new_plate = Plate(
            size=plate_size,
            quantity=int(data.get('quantity', 0)),
            threshold=int(data.get('threshold', 50))
        )
        
        db.session.add(new_plate)
        
        # Add initial stock transaction if quantity > 0
        if new_plate.quantity > 0:
            transaction = Transaction(
                plate_size=plate_size,
                quantity=new_plate.quantity,
                type='in',
                source='manual',
                notes=f'Initial stock for new plate size {plate_size}'
            )
            db.session.add(transaction)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'plate': new_plate.to_dict(),
            'message': f'Plate size {plate_size} added successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/transactions')
def get_transactions():
    transactions = Transaction.query.order_by(Transaction.date.desc()).limit(100).all()
    return jsonify([t.to_dict() for t in transactions])

@app.route('/api/stats')
def get_stats():
    try:
        total_plates = Plate.query.count()
        low_stock = Plate.query.filter(Plate.quantity <= Plate.threshold).count()
        pending_deliveries = InboundQueue.query.filter_by(status='pending').count()
        
        print(f"API /api/stats - Total: {total_plates}, Low: {low_stock}, Pending: {pending_deliveries}")
        
        return jsonify({
            'total_plates': total_plates,
            'low_stock': low_stock,
            'pending_deliveries': pending_deliveries
        })
    except Exception as e:
        print(f"Error in /api/stats: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/clear-all', methods=['POST'])
def clear_all_data():
    try:
        # Clear all tables
        Transaction.query.delete()
        InboundQueue.query.delete()
        Plate.query.delete()
        db.session.commit()
        print("All database tables cleared successfully")
        return jsonify({'success': True, 'message': 'All data cleared'})
    except Exception as e:
        db.session.rollback()
        print(f"Error clearing database: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/generate-pallet-id')
def generate_pallet_id():
    timestamp = str(int(datetime.now().timestamp()))[-6:]
    random_num = str(uuid.uuid4().int)[:3]
    pallet_id = f"PLT{timestamp}{random_num}"
    return jsonify({'pallet_id': pallet_id})

@app.route('/api/import-csv', methods=['POST'])
def import_csv():
    """Import pallet data from CSV"""
    try:
        data = request.json
        csv_content = data.get('csv_content', '')
        
        if not csv_content:
            return jsonify({'error': 'No CSV content provided'}), 400
        
        lines = csv_content.strip().split('\n')
        
        successful = 0
        errors = []
        processed_ids = set()
        
        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue
                
            # Skip header if present
            if line_num == 1 and 'WxL' in line and 'PQ' in line:
                continue
            
            try:
                # Parse the pipe-separated format: WxL|PQ|BQ|UQID
                parts = line.split('|')
                if len(parts) != 4:
                    errors.append(f"Line {line_num}: Invalid format - expected 4 fields, got {len(parts)}")
                    continue
                
                plate_size = parts[0].strip()
                boxes = parts[1].strip()
                plates_per_box = parts[2].strip()
                pallet_id = parts[3].strip()
                
                # Validate plate size format
                if not re.match(r'^\d+x\d+

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

# Initialize database when module is imported (for gunicorn)
init_db(), plate_size):
                    errors.append(f"Line {line_num}: Invalid plate size format '{plate_size}' - expected format like '76x254'")
                    continue
                
                # Validate numeric fields
                try:
                    boxes = int(boxes)
                    plates_per_box = int(plates_per_box)
                except ValueError:
                    errors.append(f"Line {line_num}: PQ and BQ must be numbers")
                    continue
                
                if boxes <= 0 or plates_per_box <= 0:
                    errors.append(f"Line {line_num}: PQ and BQ must be positive numbers")
                    continue
                
                # Check for duplicate ID in this import
                if pallet_id in processed_ids:
                    errors.append(f"Line {line_num}: Duplicate UQID '{pallet_id}' in this import")
                    continue
                
                # Check for duplicate in database
                existing = InboundQueue.query.filter_by(batch_id=pallet_id).first()
                if existing:
                    errors.append(f"Line {line_num}: UQID '{pallet_id}' already exists in database")
                    continue
                
                # Create plate if it doesn't exist
                plate = Plate.query.filter_by(size=plate_size).first()
                if not plate:
                    plate = Plate(
                        size=plate_size,
                        quantity=0,
                        threshold=50
                    )
                    db.session.add(plate)
                
                # Create inbound entry
                total_qty = boxes * plates_per_box
                inbound = InboundQueue(
                    plate_size=plate_size,
                    quantity=total_qty,
                    batch_id=pallet_id,
                    boxes=boxes,
                    plates_per_box=plates_per_box
                )
                db.session.add(inbound)
                
                processed_ids.add(pallet_id)
                successful += 1
                
            except Exception as e:
                errors.append(f"Line {line_num}: Error processing - {str(e)}")
                continue
        
        # Commit all successful entries
        if successful > 0:
            db.session.commit()
        
        return jsonify({
            'success': True,
            'imported': successful,
            'errors': errors,
            'message': f'Successfully imported {successful} pallet(s)' + (f' with {len(errors)} error(s)' if errors else '')
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Import failed: {str(e)}'}), 500

# Initialize database
def init_db():
    with app.app_context():
        try:
            print("=== DATABASE INITIALIZATION START ===")
            db.create_all()
            print("Database tables created successfully")
            
            # Check current state
            plate_count = Plate.query.count()
            transaction_count = Transaction.query.count()
            pending_count = InboundQueue.query.filter_by(status='pending').count()
            
            print(f"Current database state:")
            print(f"  - Plates: {plate_count}")
            print(f"  - Transactions: {transaction_count}")
            print(f"  - Pending deliveries: {pending_count}")
            print("=== DATABASE INITIALIZATION END ===")
                
        except Exception as e:
            print(f"Database initialization error: {e}")
            db.session.rollback()
            import traceback
            traceback.print_exc()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

# Initialize database when module is imported (for gunicorn)
init_db(), plate_size):
                return jsonify({'error': f'Invalid plate size format: {plate_size}'}), 400
            
            # Check for duplicate pallet ID
            existing = InboundQueue.query.filter_by(batch_id=pallet_id).first()
            if existing:
                return jsonify({'error': 'Pallet ID already exists'}), 400
            
            # Create plate if it doesn't exist
            plate = Plate.query.filter_by(size=plate_size).first()
            if not plate:
                plate = Plate(
                    size=plate_size,
                    quantity=0,
                    threshold=50
                )
                db.session.add(plate)
            
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
        db.session.rollback()
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
    else:
        # Create new plate if it doesn't exist
        plate = Plate(
            size=inbound.plate_size,
            quantity=inbound.quantity,
            threshold=50  # Default threshold
        )
        db.session.add(plate)
    
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

@app.route('/api/plates/new', methods=['POST'])
def add_new_plate():
    data = request.json
    
    # Validate input
    plate_size = data.get('plate_size', '').strip()
    if not plate_size:
        return jsonify({'error': 'Plate size is required'}), 400
    
    # Validate format (e.g., 100x200)
    if not re.match(r'^\d+x\d+$', plate_size):
        return jsonify({'error': 'Invalid plate size format. Use format: WIDTHxHEIGHT (e.g., 100x200)'}), 400
    
    # Check if plate already exists
    existing = Plate.query.filter_by(size=plate_size).first()
    if existing:
        return jsonify({'error': f'Plate size {plate_size} already exists'}), 400
    
    # Create new plate
    try:
        new_plate = Plate(
            size=plate_size,
            quantity=int(data.get('quantity', 0)),
            threshold=int(data.get('threshold', 50))
        )
        
        db.session.add(new_plate)
        
        # Add initial stock transaction if quantity > 0
        if new_plate.quantity > 0:
            transaction = Transaction(
                plate_size=plate_size,
                quantity=new_plate.quantity,
                type='in',
                source='manual',
                notes=f'Initial stock for new plate size {plate_size}'
            )
            db.session.add(transaction)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'plate': new_plate.to_dict(),
            'message': f'Plate size {plate_size} added successfully'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/transactions')
def get_transactions():
    transactions = Transaction.query.order_by(Transaction.date.desc()).limit(100).all()
    return jsonify([t.to_dict() for t in transactions])

@app.route('/api/stats')
def get_stats():
    try:
        total_plates = Plate.query.count()
        low_stock = Plate.query.filter(Plate.quantity <= Plate.threshold).count()
        pending_deliveries = InboundQueue.query.filter_by(status='pending').count()
        
        print(f"API /api/stats - Total: {total_plates}, Low: {low_stock}, Pending: {pending_deliveries}")
        
        return jsonify({
            'total_plates': total_plates,
            'low_stock': low_stock,
            'pending_deliveries': pending_deliveries
        })
    except Exception as e:
        print(f"Error in /api/stats: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/clear-all', methods=['POST'])
def clear_all_data():
    try:
        # Clear all tables
        Transaction.query.delete()
        InboundQueue.query.delete()
        Plate.query.delete()
        db.session.commit()
        print("All database tables cleared successfully")
        return jsonify({'success': True, 'message': 'All data cleared'})
    except Exception as e:
        db.session.rollback()
        print(f"Error clearing database: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/generate-pallet-id')
def generate_pallet_id():
    timestamp = str(int(datetime.now().timestamp()))[-6:]
    random_num = str(uuid.uuid4().int)[:3]
    pallet_id = f"PLT{timestamp}{random_num}"
    return jsonify({'pallet_id': pallet_id})

@app.route('/api/import-csv', methods=['POST'])
def import_csv():
    """Import pallet data from CSV"""
    try:
        data = request.json
        csv_content = data.get('csv_content', '')
        
        if not csv_content:
            return jsonify({'error': 'No CSV content provided'}), 400
        
        lines = csv_content.strip().split('\n')
        
        successful = 0
        errors = []
        processed_ids = set()
        
        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue
                
            # Skip header if present
            if line_num == 1 and 'WxL' in line and 'PQ' in line:
                continue
            
            try:
                # Parse the pipe-separated format: WxL|PQ|BQ|UQID
                parts = line.split('|')
                if len(parts) != 4:
                    errors.append(f"Line {line_num}: Invalid format - expected 4 fields, got {len(parts)}")
                    continue
                
                plate_size = parts[0].strip()
                boxes = parts[1].strip()
                plates_per_box = parts[2].strip()
                pallet_id = parts[3].strip()
                
                # Validate plate size format
                if not re.match(r'^\d+x\d+

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

# Initialize database when module is imported (for gunicorn)
init_db(), plate_size):
                    errors.append(f"Line {line_num}: Invalid plate size format '{plate_size}' - expected format like '76x254'")
                    continue
                
                # Validate numeric fields
                try:
                    boxes = int(boxes)
                    plates_per_box = int(plates_per_box)
                except ValueError:
                    errors.append(f"Line {line_num}: PQ and BQ must be numbers")
                    continue
                
                if boxes <= 0 or plates_per_box <= 0:
                    errors.append(f"Line {line_num}: PQ and BQ must be positive numbers")
                    continue
                
                # Check for duplicate ID in this import
                if pallet_id in processed_ids:
                    errors.append(f"Line {line_num}: Duplicate UQID '{pallet_id}' in this import")
                    continue
                
                # Check for duplicate in database
                existing = InboundQueue.query.filter_by(batch_id=pallet_id).first()
                if existing:
                    errors.append(f"Line {line_num}: UQID '{pallet_id}' already exists in database")
                    continue
                
                # Create plate if it doesn't exist
                plate = Plate.query.filter_by(size=plate_size).first()
                if not plate:
                    plate = Plate(
                        size=plate_size,
                        quantity=0,
                        threshold=50
                    )
                    db.session.add(plate)
                
                # Create inbound entry
                total_qty = boxes * plates_per_box
                inbound = InboundQueue(
                    plate_size=plate_size,
                    quantity=total_qty,
                    batch_id=pallet_id,
                    boxes=boxes,
                    plates_per_box=plates_per_box
                )
                db.session.add(inbound)
                
                processed_ids.add(pallet_id)
                successful += 1
                
            except Exception as e:
                errors.append(f"Line {line_num}: Error processing - {str(e)}")
                continue
        
        # Commit all successful entries
        if successful > 0:
            db.session.commit()
        
        return jsonify({
            'success': True,
            'imported': successful,
            'errors': errors,
            'message': f'Successfully imported {successful} pallet(s)' + (f' with {len(errors)} error(s)' if errors else '')
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Import failed: {str(e)}'}), 500

# Initialize database
def init_db():
    with app.app_context():
        try:
            print("=== DATABASE INITIALIZATION START ===")
            db.create_all()
            print("Database tables created successfully")
            
            # Check current state
            plate_count = Plate.query.count()
            transaction_count = Transaction.query.count()
            pending_count = InboundQueue.query.filter_by(status='pending').count()
            
            print(f"Current database state:")
            print(f"  - Plates: {plate_count}")
            print(f"  - Transactions: {transaction_count}")
            print(f"  - Pending deliveries: {pending_count}")
            print("=== DATABASE INITIALIZATION END ===")
                
        except Exception as e:
            print(f"Database initialization error: {e}")
            db.session.rollback()
            import traceback
            traceback.print_exc()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

# Initialize database when module is imported (for gunicorn)
init_db()
