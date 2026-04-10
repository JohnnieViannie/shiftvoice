import requests

# Test Login
url = "http://localhost:8080/api/auth/login/"
data = {
    "username": "test@example.com",
    "password": "password123"
}
res = requests.post(url, json=data)
print("LOGIN:", res.status_code, res.text)
