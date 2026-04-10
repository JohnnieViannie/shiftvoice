import requests
import json

url = "http://localhost:8080/api/auth/register/"
data = {
    "email": "test@example.com",
    "password": "password123",
    "fullName": "Test User",
    "companyName": "Test Inc"
}

res = requests.post(url, json=data)
print("REGISTER:", res.status_code, res.text)
