import asyncio
import websockets
import json
import logging
from google.generativeai import GenerativeModel
import google.generativeai as genai
from typing import List, Dict, AsyncGenerator
from collections import defaultdict
import weakref
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Gather
from aiohttp import web
import csv
import os
from dotenv import load_dotenv
import asyncio
import websockets
from typing import List, Dict, AsyncGenerator
from collections import defaultdict
import weakref
import aiofiles
from gtts import gTTS
import time
from io import StringIO
from gtts import gTTS
from io import BytesIO, StringIO

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Configuration from environment variables
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
NGROK_URL = "https://project-car-sales-2.onrender.com"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Initialize Twilio client
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Define prompts
CHAT_PROMPT = (
    "You are Sam, a professional car salesperson. Always respond with short, sales-focused answers under 10 seconds. "
    "Concentrate on car features, specifications, and addressing customer needs concisely."
)

VOICE_PROMPT = (
    "You are Sam, a professional car salesperson. Always respond with short, sales-focused answers under 10 seconds. "
    "Concentrate on car features, specifications, and addressing customer needs concisely."
)

GREETING = (
    "Hello! I'm Sam, your virtual car salesperson. What kind of car are you looking for today?"  
)

# Configure Gemini API
genai.configure(api_key=GEMINI_API_KEY)
model = GenerativeModel("gemini-2.0-flash-exp")

class ConversationManager:
    def __init__(self):
        self.chat_conversations = defaultdict(list)
        self.call_conversations = defaultdict(list)
        self._max_history = 10
        self.sales_data = []

    def add_chat_message(self, websocket, role: str, content: str):
        self.chat_conversations[websocket].append({"role": role, "content": content})
        if len(self.chat_conversations[websocket]) > self._max_history:
            self.chat_conversations[websocket] = self.chat_conversations[websocket][-self._max_history:]

    def get_chat_conversation(self, websocket) -> List[Dict[str, str]]:
        return self.chat_conversations[websocket]

    def initialize_chat(self, websocket):
        if not self.chat_conversations[websocket]:
            self.chat_conversations[websocket].append({"role": "user", "content": CHAT_PROMPT})

    async def handle_voice_call(self, call_sid: str, user_input: str = None) -> str:
        if user_input:
            self.call_conversations[call_sid].append({"role": "user", "content": user_input})
        
        conversation = self.call_conversations[call_sid]
        if not conversation:
            conversation.append({"role": "user", "content": VOICE_PROMPT})
        
        response_text = ""
        async for chunk in generate(conversation):
            response_text += chunk
        
        self.call_conversations[call_sid].append({"role": "assistant", "content": response_text})
        return response_text

    def clear_conversation(self, websocket):
        self.chat_conversations.pop(websocket, None)

    def save_sale(self, name: str, phone: str, conversation: List[Dict[str, str]]):
        self.sales_data.append({
            "name": name,
            "phone": phone,
            "conversation": conversation
        })
        logging.info(f"Sale saved: {name}, {phone}")

class ClientManager:
    def __init__(self):
        self.clients = weakref.WeakSet()
        self.conversation_manager = ConversationManager()

    def add_client(self, websocket):
        self.clients.add(websocket)
        logging.info(f"Client added. Total clients: {len(self.clients)}")

    def remove_client(self, websocket):
        self.clients.discard(websocket)
        self.conversation_manager.clear_conversation(websocket)
        logging.info(f"Client removed. Total clients: {len(self.clients)}")

client_manager = ClientManager()

def text_to_speech(text: str) -> bytes:
    """Convert text to speech using Google Text-to-Speech"""
    try:
        tts = gTTS(text=text, lang='en', slow=False)
        # Save to bytes buffer instead of file
        audio_buffer = BytesIO()
        tts.write_to_fp(audio_buffer)
        audio_buffer.seek(0)
        return audio_buffer.read()
    except Exception as e:
        logging.error(f"Error converting text to speech: {str(e)}")
        return None

async def generate(
    messages: List[Dict[str, str]],
    temperature: float = 0.8,
    max_tokens: int = 512,
) -> AsyncGenerator[str, None]:
    try:
        formatted_messages = [
            {
                "role": "model" if msg["role"] == "assistant" else "user",
                "parts": [{"text": msg["content"]}],
            }
            for msg in messages
        ]

        response = model.generate_content(
            formatted_messages,
            generation_config={
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            },
            stream=True,
        )

        full_response = ""
        for chunk in response:
            if hasattr(chunk, "text") and chunk.text:
                full_response += chunk.text
                yield chunk.text
                await asyncio.sleep(0.01)

    except Exception as e:
        logging.error(f"Generation error: {str(e)}")
        yield f"Error: {str(e)}"

async def generateChat(
    websocket,
    messages: List[Dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 2024,
) -> AsyncGenerator[str, None]:
    try:
        formatted_messages = [
            {
                "role": "model" if msg["role"] == "assistant" else "user",
                "parts": [{"text": msg["content"]}],
            }
            for msg in messages
        ]

        response = model.generate_content(
            formatted_messages,
            generation_config={
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            },
            stream=True,
        )

        full_response = ""
        for chunk in response:
            if hasattr(chunk, "text") and chunk.text:
                full_response += chunk.text
                x = str(chunk.text)
                y = x.replace("*", " ")
                yield y

                audio_data = text_to_speech(y)
                if audio_data:
                    logging.debug(f"Generated audio data size: {len(audio_data)} bytes")
                    await websocket.send(audio_data)

                await asyncio.sleep(0.01)

    except Exception as e:
        logging.error(f"Generation error: {str(e)}")
        yield f"Error: {str(e)}"

async def handle_message(websocket, user_message: str) -> str:
    client_manager.conversation_manager.add_chat_message(websocket, "user", user_message)
    conversation_history = client_manager.conversation_manager.get_chat_conversation(websocket)

    full_response = ""
    async for response_chunk in generateChat(websocket, conversation_history):
        if response_chunk:
            full_response += response_chunk
            await websocket.send(response_chunk)
            await asyncio.sleep(0.1)

    return full_response

async def process_csv_and_call(file_content: str):
    csv_file = StringIO(file_content)
    csv_reader = csv.DictReader(csv_file)
    
    for row in csv_reader:
        name = row.get('name')
        phone = row.get('phone')
        
        if name and phone:
            try:
                call = twilio_client.calls.create(
                    to=phone,
                    from_=TWILIO_PHONE_NUMBER,
                    url=f"{NGROK_URL}/voice"
                )
                logging.info(f"Initiated call to {name} at {phone}: {call.sid}")
            except Exception as e:
                logging.error(f"Error calling {name} at {phone}: {str(e)}")

# Create aiohttp app for Twilio webhooks
app = web.Application()

async def voice_webhook(request):
    response = VoiceResponse()
    response.say(GREETING)
    
    gather = Gather(input='speech', timeout=3, action='/handle-response')
    response.append(gather)
    
    return web.Response(text=str(response), content_type='text/xml')

async def handle_voice_response(request):
    data = await request.post()
    call_sid = data.get('CallSid')
    user_speech = data.get('SpeechResult')
    
    ai_response = await client_manager.conversation_manager.handle_voice_call(call_sid, user_speech)
    
    response = VoiceResponse()
    response.say(ai_response)
    
    gather = Gather(input='speech', timeout=3, action='/handle-response')
    response.append(gather)
    
    return web.Response(text=str(response), content_type='text/xml')

# Set up routes
app.router.add_post('/voice', voice_webhook)
app.router.add_post('/handle-response', handle_voice_response)

async def handle_client(websocket):
    remote_address = websocket.remote_address if hasattr(websocket, "remote_address") else "unknown"

    try:
        client_manager.add_client(websocket)
        logging.info(f"New client connected from {remote_address}")

        client_manager.conversation_manager.initialize_chat(websocket)
        await websocket.send(GREETING)

        async for message in websocket:
            try:
                data = json.loads(message)
                
                if "csv_content" in data:
                    await process_csv_and_call(data["csv_content"])
                    await websocket.send("CSV processed and calls initiated")
                    continue
                    
                if "content" not in data:
                    await websocket.send("Error: Message must contain 'content' field")
                    continue

                user_message = data["content"]
                if "confirm sale" in user_message.lower():
                    await websocket.send("Please provide your name and phone number.")
                    data = await websocket.recv()
                    details = json.loads(data)
                    name = details.get("name")
                    phone = details.get("phone")
                    if name and phone:
                        conversation = client_manager.conversation_manager.get_chat_conversation(websocket)
                        client_manager.conversation_manager.save_sale(name, phone, conversation)
                        await websocket.send("Thank you! The sale has been confirmed.")
                    else:
                        await websocket.send("Error: Missing name or phone number.")
                else:
                    full_response = await handle_message(websocket, user_message)
                    if full_response:
                        client_manager.conversation_manager.add_chat_message(websocket, "assistant", full_response)

            except json.JSONDecodeError:
                await websocket.send("Error: Invalid JSON format")
            except Exception as e:
                logging.error(f"Error handling message: {str(e)}")
                await websocket.send(f"Error: {str(e)}")

    except websockets.exceptions.ConnectionClosed:
        logging.info(f"Client {remote_address} disconnected normally")
    except Exception as e:
        logging.error(f"Connection error for {remote_address}: {str(e)}")
    finally:
        client_manager.remove_client(websocket)

async def main():
    try:
        # Get port from environment variable or use default
        port = int(os.getenv("PORT", 5000))
        
        # Start WebSocket server
        websocket_server = await websockets.serve(
            handle_client,
            "0.0.0.0",  # Listen on all interfaces
            port,
            ping_interval=20,
            ping_timeout=10,
        )
        logging.info(f"WebSocket server started on port {port}")

        # Start aiohttp server for Twilio webhooks
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port + 1)  # Use next port for webhook server
        await site.start()
        logging.info(f"Twilio webhook server started on port {port + 1}")

        await asyncio.Future()  # run forever

    except Exception as e:
        logging.error(f"Server error: {str(e)}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("\nServer stopped by user")
    except Exception as e:
        logging.error(f"Fatal error: {str(e)}")
