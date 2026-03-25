import os
from dotenv import load_dotenv 
import urllib.parse #builds url
from flask import Flask, request
import requests #http requests
import base64 #encoding and decoding
import threading 
import json
from datetime import datetime, timedelta



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

#parameters for fitbit
params = {
    "response_type": "code",
    "client_id": CLIENT_ID,
    "redirect_uri": REDIRECT_URI,
    "scope": "activity heartrate sleep profile",  
}

#link for user to sign in
auth_url = "https://www.fitbit.com/oauth2/authorize?" + urllib.parse.urlencode(params)

#create flask object
app = Flask(__name__)

#starts flask server
def start_server():
    #starts running flask server in a background threat, UI stays responsive
    thread = threading.Thread(target = lambda: app.run(port = 8080, debug = True, use_reloader = False))
    thread.daemon = True
    thread.start()
    
#opens fitbit login webpage
def start_auth_flow():
    import webbrowser
    webbrowser.open(auth_url)
    
#exchange authorization code for API access token
def exchange_code_for_token(auth_code):
    #url to grab fitbit api token
    TOKEN_URL = "https://api.fitbit.com/oauth2/token"
    
    #POST request 
    headers = get_auth_headers()
    data = get_token_data("authorization_code", code = auth_code)
    
    response = requests.post(TOKEN_URL, headers = headers, data = data)
    
    #convert this response into a JSON string
    token_data = response.json()
    
    return token_data

#saves token to tokens.json
def save_tokens(token_data):
    with open("tokens.json", "w") as token_file:
        json.dump(token_data, token_file, indent = 4)   

#loads token from tokens.json file
def load_tokens():
    try:
        with open("tokens.json", "r") as token_file:
            return json.load(token_file)
    except FileNotFoundError:
        return None
    
#refreshes the access token
def refresh_access_token(refresh_token_value):
    headers = get_auth_headers()
    data = get_token_data("refresh_token", refresh_token = refresh_token_value)
    
    #create new POST request 
    response = requests.post(TOKEN_URL, headers = headers, data = data)
    token_data = response.json()
    
    #saving new token or throwing exception 
    if 'access_token' in token_data:
        save_tokens(token_data)
        return token_data
    else:
        raise Exception(f"Token refresh failed: {token_data}")
    
#checks for token validity and refreshes if its expired 
def check_token_expiry():
    tokens = load_tokens()
    if not tokens:
        return None
    
    #checking expiry 
    expires_at = tokens.get('expires_at')
    if expires_at:
        expiry_time = datetime.fromtimestamp(expires_at)
        if expiry_time < datetime.now():
            print("token is expired, refreshing...")
            return refresh_access_token(tokens['refresh_token'])
        
    return tokens

@app.route("/callback")
def callback():
    code = request.args.get("code")
    token_data = exchange_code_for_token(code)
    save_tokens(token_data)
    return "auth success! close this window and return to app!"

if __name__ == "__main__":
    app.run(port = 8080, debug = True)