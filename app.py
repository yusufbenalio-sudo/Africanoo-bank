from flask import Flask, request, jsonify, send_from_directory
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from flask_bcrypt import Bcrypt
from datetime import datetime, timedelta
import os

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "secret-key-123")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///bank.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["JWT_SECRET_KEY"] = "jwt-secret-123"
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(days=7)

from flask_sqlalchemy import SQLAlchemy
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
jwt = JWTManager(app)

# Models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    pin = db.Column(db.String(256), nullable=False)
    country = db.Column(db.String(10), nullable=False)
    wallet_balance = db.Column(db.Float, default=0.0)
    bonus_balance = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    type = db.Column(db.String(20), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(3), nullable=False)
    status = db.Column(db.String(20), default="pending")
    description = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Betting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    selection = db.Column(db.String(100), nullable=False)
    odds = db.Column(db.Float, nullable=False)
    potential_win = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Auth Routes
@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json()
    required = ["username", "email", "phone", "pin", "country"]
    for field in required:
        if field not in data:
            return jsonify({"error": f"{field} is required"}), 400

    if data["country"] not in ["TCD", "NGA"]:
        return jsonify({"error": "Country must be TCD or NGA"}), 400

    if User.query.filter_by(phone=data["phone"]).first():
        return jsonify({"error": "Phone already registered"}), 400

    hashed_pin = bcrypt.generate_password_hash(data["pin"]).decode("utf-8")
    user = User(username=data["username"], email=data["email"], phone=data["phone"], pin=hashed_pin, country=data["country"])
    db.session.add(user)
    db.session.commit()

    access_token = create_access_token(identity=user.id)
    return jsonify({"message": "Registered", "user": {"id": user.id, "username": user.username, "country": user.country}, "access_token": access_token}), 201

@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json()
    phone = data.get("phone")
    pin = data.get("pin")

    user = User.query.filter_by(phone=phone).first()
    if not user or not bcrypt.check_password_hash(user.pin, pin):
        return jsonify({"error": "Invalid credentials"}), 401

    access_token = create_access_token(identity=user.id)
    return jsonify({"message": "Login OK", "user": {"id": user.id, "username": user.username, "country": user.country, "balance": user.wallet_balance}, "access_token": access_token})

# Wallet Routes
@app.route("/api/wallet/balance", methods=["GET"])
@jwt_required()
def get_balance():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    currency = "XAF" if user.country == "TCD" else "NGN"
    return jsonify({"wallet_balance": user.wallet_balance, "bonus_balance": user.bonus_balance, "currency": currency, "country": user.country})

@app.route("/api/wallet/fund", methods=["POST"])
@jwt_required()
def fund_wallet():
    user_id = get_jwt_identity()
    data = request.get_json()
    amount = data.get("amount", 0)

    if amount <= 0:
        return jsonify({"error": "Invalid amount"}), 400

    user = User.query.get(user_id)
    user.wallet_balance += amount
    currency = "XAF" if user.country == "TCD" else "NGN"
    tx = Transaction(user_id=user_id, type="deposit", amount=amount, currency=currency, status="completed", description="Wallet funding")
    db.session.add(tx)
    db.session.commit()

    return jsonify({"message": "Funded", "new_balance": user.wallet_balance})

@app.route("/api/wallet/transfer", methods=["POST"])
@jwt_required()
def transfer():
    user_id = get_jwt_identity()
    data = request.get_json()
    recipient_phone = data.get("recipient_phone")
    amount = data.get("amount", 0)

    if not recipient_phone or amount <= 0:
        return jsonify({"error": "Phone and amount required"}), 400

    sender = User.query.get(user_id)
    recipient = User.query.filter_by(phone=recipient_phone).first()

    if not recipient:
        return jsonify({"error": "Recipient not found"}), 404
    if sender.wallet_balance < amount:
        return jsonify({"error": "Insufficient balance"}), 400

    sender.wallet_balance -= amount
    recipient.wallet_balance += amount
    currency = "XAF" if sender.country == "TCD" else "NGN"

    db.session.add(Transaction(user_id=user_id, type="transfer", amount=amount, currency=currency, status="completed", description=f"To {recipient.username}"))
    db.session.add(Transaction(user_id=recipient.id, type="transfer", amount=amount, currency=currency, status="completed", description=f"From {sender.username}"))
    db.session.commit()

    return jsonify({"message": "Transfer done", "amount": amount, "new_balance": sender.wallet_balance})

@app.route("/api/wallet/transactions", methods=["GET"])
@jwt_required()
def get_transactions():
    user_id = get_jwt_identity()
    txs = Transaction.query.filter_by(user_id=user_id).order_by(Transaction.created_at.desc()).limit(50).all()
    return jsonify({"transactions": [{"id": t.id, "type": t.type, "amount": t.amount, "currency": t.currency, "status": t.status, "description": t.description, "created_at": t.created_at.isoformat()} for t in txs]})

# Services Routes
@app.route("/api/services/data-plans", methods=["GET"])
@jwt_required()
def data_plans():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    plans = {
        "TCD": [{"id": 1, "name": "Airtel 500MB", "price": 500}, {"id": 2, "name": "Airtel 1GB", "price": 900}, {"id": 3, "name": "Airtel 5GB", "price": 3500}],
        "NGA": [{"id": 1, "name": "MTN 1GB", "price": 300}, {"id": 2, "name": "MTN 2GB", "price": 500}, {"id": 3, "name": "MTN 5GB", "price": 1000}]
    }
    return jsonify({"country": user.country, "currency": "XAF" if user.country == "TCD" else "NGN", "plans": plans.get(user.country, [])})

@app.route("/api/services/buy-data", methods=["POST"])
@jwt_required()
def buy_data():
    user_id = get_jwt_identity()
    data = request.get_json()
    plan_id = data.get("plan_id")
    phone_number = data.get("phone_number")

    user = User.query.get(user_id)
    prices = {"TCD": {1: 500, 2: 900, 3: 3500}, "NGA": {1: 300, 2: 500, 3: 1000}}
    price = prices.get(user.country, {}).get(plan_id, 0)

    if not price or user.wallet_balance < price:
        return jsonify({"error": "Invalid plan or insufficient balance"}), 400

    user.wallet_balance -= price
    currency = "XAF" if user.country == "TCD" else "NGN"
    db.session.add(Transaction(user_id=user_id, type="data", amount=price, currency=currency, status="completed", description=f"Data plan {plan_id}"))
    db.session.commit()

    return jsonify({"message": "Data purchased", "amount": price, "phone": phone_number, "new_balance": user.wallet_balance})

@app.route("/api/services/billers", methods=["GET"])
@jwt_required()
def billers():
    user_id = get_jwt_identity()
    user = User.query.get(user_id)
    billers = {"TCD": [{"id": 1, "name": "Chad Electric", "category": "electricity"}], "NGA": [{"id": 1, "name": "NEPA", "category": "electricity"}, {"id": 2, "name": "DSTV", "category": "tv"}]}
    return jsonify({"billers": billers.get(user.country, [])})

@app.route("/api/services/pay-bill", methods=["POST"])
@jwt_required()
def pay_bill():
    user_id = get_jwt_identity()
    data = request.get_json()
    amount = data.get("amount", 0)

    if amount <= 0:
        return jsonify({"error": "Invalid amount"}), 400

    user = User.query.get(user_id)
    if user.wallet_balance < amount:
        return jsonify({"error": "Insufficient balance"}), 400

    user.wallet_balance -= amount
    currency = "XAF" if user.country == "TCD" else "NGN"
    db.session.add(Transaction(user_id=user_id, type="bill", amount=amount, currency=currency, status="completed", description="Bill payment"))
    db.session.commit()

    return jsonify({"message": "Bill paid", "amount": amount, "new_balance": user.wallet_balance})

@app.route("/api/services/betting/matches", methods=["GET"])
@jwt_required()
def matches():
    return jsonify({"matches": [{"id": 1, "home": "Chad", "away": "Nigeria", "home_win": 3.5, "draw": 2.8, "away_win": 2.1}, {"id": 2, "home": "Arsenal", "away": "Liverpool", "home_win": 2.5, "draw": 3.2, "away_win": 2.8}]})

@app.route("/api/services/place-bet", methods=["POST"])
@jwt_required()
def place_bet():
    user_id = get_jwt_identity()
    data = request.get_json()
    match_id = data.get("match_id")
    selection = data.get("selection")
    amount = data.get("amount", 0)

    if amount <= 0:
        return jsonify({"error": "Invalid amount"}), 400

    user = User.query.get(user_id)
    if user.wallet_balance < amount:
        return jsonify({"error": "Insufficient balance"}), 400

    odds_map = {1: {"home_win": 3.5, "draw": 2.8, "away_win": 2.1}, 2: {"home_win": 2.5, "draw": 3.2, "away_win": 2.8}}
    odds = odds_map.get(match_id, {}).get(selection, 2.0)
    potential_win = amount * odds

    user.wallet_balance -= amount
    currency = "XAF" if user.country == "TCD" else "NGN"
    db.session.add(Betting(user_id=user_id, amount=amount, selection=selection, odds=odds, potential_win=potential_win, status="pending"))
    db.session.add(Transaction(user_id=user_id, type="bet", amount=amount, currency=currency, status="pending", description=f"Bet on match {match_id}"))
    db.session.commit()

    return jsonify({"message": "Bet placed", "stake": amount, "odds": odds, "potential_win": potential_win, "new_balance": user.wallet_balance})

@app.route("/api/services/betting/history", methods=["GET"])
@jwt_required()
def bet_history():
    user_id = get_jwt_identity()
    bets = Betting.query.filter_by(user_id=user_id).order_by(Betting.created_at.desc()).limit(20).all()
    return jsonify({"bets": [{"id": b.id, "amount": b.amount, "selection": b.selection, "odds": b.odds, "potential_win": b.potential_win, "status": b.status} for b in bets]})

# Health check
@app.route("/")
def home():
    return jsonify({"status": "OK", "app": "Banking API - Chad & Nigeria"})

with app.app_context():
    db.create_all()

@app.route("/")
def home():
    return send_from_directory("templates", "index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
