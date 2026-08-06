#!/usr/bin/env python3
"""
MQTT Logger for Armband PPG Data
Subscribes to MQTT topic and logs JSON payloads to SQLite DB
"""

import json
import sqlite3
import paho.mqtt.client as mqtt
from datetime import datetime
import os

# Configuration
MQTT_BROKER = "localhost"  # Change to your broker IP
MQTT_PORT = 1883
MQTT_TOPIC = "armband/ppg/data"  # Match your firmware topic
DB_PATH = "armband_data.db"


def init_database():
    """Create SQLite database and table if not exists"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ppg_readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            heart_rate REAL,
            spo2 REAL,
            temperature REAL,
            motion_x REAL,
            motion_y REAL,
            motion_z REAL,
            reflectance_940 REAL,
            raw_json TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    print(f"Database initialized: {DB_PATH}")


def log_to_db(data):
    """Insert reading into database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    timestamp = data.get('timestamp', datetime.now().isoformat())
    
    cursor.execute('''
        INSERT INTO ppg_readings 
        (timestamp, heart_rate, spo2, temperature, motion_x, motion_y, motion_z, reflectance_940, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        timestamp,
        data.get('heart_rate'),
        data.get('spo2'),
        data.get('temperature'),
        data.get('motion', {}).get('x'),
        data.get('motion', {}).get('y'),
        data.get('motion', {}).get('z'),
        data.get('reflectance_940'),
        json.dumps(data)
    ))
    
    conn.commit()
    conn.close()


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"Connected to MQTT broker at {MQTT_BROKER}")
        client.subscribe(MQTT_TOPIC)
        print(f"Subscribed to topic: {MQTT_TOPIC}")
    else:
        print(f"Failed to connect, return code {rc}")


def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode('utf-8')
        data = json.loads(payload)
        log_to_db(data)
        print(f"Logged reading at {data.get('timestamp', 'unknown time')}")
    except json.JSONDecodeError:
        print(f"Invalid JSON received: {msg.payload}")
    except Exception as e:
        print(f"Error processing message: {e}")


def main():
    init_database()
    
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    
    try:
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        print("Starting MQTT logger...")
        client.loop_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        client.disconnect()
    except Exception as e:
        print(f"Connection error: {e}")


if __name__ == "__main__":
    main()