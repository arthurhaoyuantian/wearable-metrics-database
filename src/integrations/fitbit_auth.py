import base64  # encoding and decoding
import json
import logging
import os
import threading
import urllib.parse  # builds url

import requests  # http requests
from dotenv import load_dotenv
from flask import Flask, request

LOG = logging.getLogger(__name__)

#load and access .env variables
load_dotenv()
CLIENT_ID = os.getenv("FITBIT_CLIENT_ID")
CLIENT_SECRET = os.getenv("FITBIT_CLIENT_SECRET")
REDIRECT_URI = os.getenv("FITBIT_REDIRECT_URI")
TOKEN_URL = "https://api.fitbit.com/oauth2/token"

#getter function 
def get_auth_headers():
    #encoding into b64 to communicate with oauth
    client_creds = f"{CLIENT_ID}:{CLIENT_SECRET}"
    client_creds_b64 = base64.b64encode(client_creds.encode()).decode()
    
    #POST request
    return {
        "Authorization": f"Basic {client_creds_b64}",
        "Content-Type":"application/x-www-form-urlencoded" 
    }

#getter function
def get_token_data(grant_type, **kwargs):
    data = {
        "client_id": CLIENT_ID,
        "grant_type": grant_type,
    }
    
    if grant_type == "authorization_code":
        data.update({
            "redirect_uri": REDIRECT_URI,
            "code": kwargs["code"]
        })
    elif grant_type == "refresh_token":
        data.update({
            "refresh_token": kwargs["refresh_token"]
        })
    
    return data

#create flask object
app = Flask(__name__)

#starts flask server
def start_server():
    """Run Flask OAuth callback server in a background thread (UI stays responsive)."""
    thread = threading.Thread(
        target=lambda: app.run(port=8080, debug=True, use_reloader=False)
    )
    thread.daemon = True
    thread.start()
    LOG.info("Fitbit OAuth callback server thread started (port 8080)")
    
#opens fitbit login webpage
def start_auth_flow(patient_id):
    import webbrowser

    LOG.info("Opening Fitbit authorize URL in browser for patient_id=%s", patient_id)
    # parameters for fitbit
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "activity heartrate sleep profile oxygen_saturation",  
        "prompt": "login",
        "state": patient_id
    }
    #link for user to sign in
    auth_url = "https://www.fitbit.com/oauth2/authorize?" + urllib.parse.urlencode(params)

    webbrowser.open(auth_url)
    
#exchange authorization code for API access token
def exchange_code_for_token(auth_code):
    #url to grab fitbit api token
    TOKEN_URL = "https://api.fitbit.com/oauth2/token"
    
    #POST request 
    headers = get_auth_headers()
    data = get_token_data("authorization_code", code = auth_code)
    
    response = requests.post(TOKEN_URL, headers=headers, data=data)
    if response.status_code != 200:
        LOG.error(
            "exchange_code_for_token: HTTP %s from token endpoint (body truncated)",
            response.status_code,
        )
    token_data = response.json()
    return token_data

# saves token to patient in database
def save_tokens(db, patient_id, token_data):
    LOG.info("Writing Fitbit OAuth tokens to DB for patient_id=%s", patient_id)
    db.update_patient_info(
        patient_id,
        fitbit_access_token=token_data["access_token"],
        fitbit_refresh_token=token_data["refresh_token"],
    )

# refreshes the access token (called from FitbitAPI on HTTP 401)
def refresh_access_token(db, patient_id, refresh_token_value):
    headers = get_auth_headers()
    data = get_token_data("refresh_token", refresh_token = refresh_token_value)
    
    #create new POST request 
    response = requests.post(TOKEN_URL, headers = headers, data = data)
    token_data = response.json()
    
    #saving new token or throwing exception 
    if "access_token" in token_data:
        save_tokens(db, patient_id, token_data)
        LOG.info("Token refresh succeeded for patient_id=%s", patient_id)
        return token_data
    LOG.error("Token refresh failed for patient_id=%s (no access_token in response)", patient_id)
    raise Exception(f"Token refresh failed: {token_data}")

_db = None

def set_db(db):
    global _db
    _db = db
@app.route("/callback")
def callback():
    from src.data.database import EHRDatabase

    code = request.args.get("code")
    patient_id = request.args.get("state")
    if not code:
        LOG.warning("OAuth /callback hit without code query param")
    LOG.info(
        "OAuth /callback received (patient_id from state=%s); exchanging code for tokens",
        patient_id,
    )
    token_data = exchange_code_for_token(code)
    db = EHRDatabase()
    save_tokens(db, patient_id, token_data)
    db.close()
    LOG.info("OAuth callback finished for patient_id=%s", patient_id)
    return "auth success! close this window and return to app!"