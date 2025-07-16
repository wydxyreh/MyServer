#!/usr/bin/env python3
import logging
import os
import sys
from datetime import datetime
from threading import Thread

from flask import Flask
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from werkzeug.serving import make_server

db = SQLAlchemy()

def get_logs_path():
    """
    Construct the logs directory path based on the script directory.

    Returns:
        str: Full path to the logs directory.
    """
    script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))  # Get the script's directory
    logs_path = os.path.join(script_dir, 'logs')
    logging.debug("Logs path constructed: %s", logs_path)
    return logs_path


def initialize_db(app):
    """
    Initialize the database by creating tables and ensuring
    that a ServerManager instance exists.
    """
    with app.app_context():
        logging.debug("Initializing the database...")
        try:
            # Create a temporary engine for establishing a connection 
            temp_engine = create_engine(
                f"mysql+pymysql://{app.config['MYSQL_USER']}:{app.config['MYSQL_PASSWORD']}"
                f"@{app.config['MYSQL_CONTAINER_IP']}:{app.config['MYSQL_PORT']}"
            )
            # Attempt to connect to the MySQL server
            try:
                temp_engine.connect()
                logging.info("Connected to MySQL server successfully.")
            except OperationalError:
                logging.critical("Could not connect to the MySQL server.")
                raise

            # Check if the database exists
            try:
                engine = create_engine(app.config['SQLALCHEMY_DATABASE_URI'])
                engine.connect()
                logging.info("Database already exists.")
            except OperationalError:
                # Create the database if it doesn't exist
                logging.debug(f"Database: {app.config['MYSQL_DATABASE']} does not exist, creating it...")
                temp_engine.execute(f'CREATE DATABASE IF NOT EXISTS {app.config["MYSQL_DATABASE"]}')
                engine = create_engine(app.config['SQLALCHEMY_DATABASE_URI'])
                db.metadata.create_all(engine)
                logging.info("Database created and tables initialized successfully.")
        except Exception as e:
            logging.critical(f"Error initializing the database: {e}")
            raise


def create_app():
    """
    Create and configure the Flask application.
    Returns the initialized Flask application instance.
    """
    app = Flask(__name__)

    # Load admin user information (hardcoded)
    app.config['ADMIN_USERNAME'] = 'admin'
    app.config['ADMIN_PASSWORD'] = 'password'
    logging.debug('Admin user information loaded successfully.')

    # Hardcoded database configuration
    db_config = {
        'MYSQL_CONTAINER_IP': '172.17.0.3',
        'MYSQL_PORT': '3306',
        'MYSQL_USER': 'root',
        'MYSQL_PASSWORD': 'password',
        'MYSQL_DATABASE': 'activateverifier'
    }

    # Load database configuration and validate it
    try:
        logging.debug(f'Database configuration: {db_config}')
        app.config.update(db_config)
        app.config['SQLALCHEMY_DATABASE_URI'] = (
            f"mysql+pymysql://{db_config['MYSQL_USER']}:{db_config['MYSQL_PASSWORD']}"
            f"@{db_config['MYSQL_CONTAINER_IP']}:{db_config['MYSQL_PORT']}/{db_config['MYSQL_DATABASE']}"
        )
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

        # Bind database to application
        db.init_app(app)
        Migrate(app, db)  # Initialize migration object
        logging.debug('Database initialization complete.')

    except Exception as e:
        logging.critical(f"Failed to create application: {e}")
        raise

    return app


class ServerThread(Thread):
    """
    A thread to run the Flask HTTP server.
    """
    def __init__(self, app, port):
        Thread.__init__(self)
        self.port = port
        self.srv = make_server('0.0.0.0', self.port, app)
        self.ctx = app.app_context()
        self.ctx.push()

    def run(self):
        """
        Run the server in the thread.
        """
        try:
            logging.info(f"Starting HTTP server on port {self.port}...")
            self.srv.serve_forever()
        except Exception as e:
            logging.critical(f"Error occurred while running the server: {e}")

    def shutdown(self):
        """
        Shutdown the server.
        """
        logging.info(f"Shutting down HTTP server on port {self.port}...")
        self.srv.shutdown()

if __name__ == '__main__':
    # Configure logging with single configuration
    logs_directory = get_logs_path()
    if not os.path.exists(logs_directory):
        os.makedirs(logs_directory)
    log_file_name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".log"
    log_file_path = os.path.join(logs_directory, log_file_name)

    # Single logging configuration with both console and file output
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file_path)
        ]
    )
    
    logging.info('Current log level: DEBUG')

    # Create Flask application
    app = create_app()

    # Initialize the database
    initialize_db(app)

    # Hardcoded HTTP port
    http_port = 5000

    # Start HTTP server thread
    http_server = None
    try:
        # Start HTTP server thread
        http_server = ServerThread(app, http_port)
        http_server.start()
        logging.info(f"HTTP server started on port {http_port}.")

        # Wait for server thread to finish
        http_server.join()
    except Exception as e:
        logging.critical(f"An error occurred in the main thread: {e}")