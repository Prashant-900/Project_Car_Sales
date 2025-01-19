# server.py
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
import asyncio
import websockets
import json
import logging
from google.generativeai import GenerativeModel
import google.generativeai as genai
from typing import List, Dict, AsyncGenerator
from collections import defaultdict
import weakref
import os
import aiofiles
from gtts import gTTS
from smallest import Smallest
import time
from io import StringIO

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Configuration
TWILIO_ACCOUNT_SID = "AC59521c7e79105029cd69d380cad8602a"
TWILIO_AUTH_TOKEN = "e3b1719cead7fb890889a053912325a7"
TWILIO_PHONE_NUMBER = "+18786669252"
NGROK_URL = "https://1f1d-14-99-167-142.ngrok-free.app" 
SMALLEST_API_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOiI2NzgwMmRkNjQzNjMwNGZhZDUwNjVkOTAiLCJrZXlOYW1lIjoiY29udmVydC1haS1yZXNwb25zZS10by1zcGVlY2giLCJpYXQiOjE3MzY4Njk5MTB9.FSOHIn58oA8K-FrpBz2h-NhCtWlp7CQ9bPTMDRuxpYA"


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
genai.configure(api_key="AIzaSyAihY7kB3c2vfMgJ5B9b_wnf3B-M2aBJIY")
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
    """Convert text to speech using the smallest.ai API with retry logic."""
    retries = 3  # Number of retries before failing
    for attempt in range(retries):
        try:
            # Initialize the Smallest client
            client = Smallest(api_key=SMALLEST_API_KEY)

            # Call the text-to-speech API
            response = client.synthesize(text)

            # Return the audio content as bytes
            return response
        except Exception as e:
            logging.error(f"Error converting text to speech: {str(e)}")
            if "rate limit" in str(e).lower() and attempt < retries - 1:
                logging.info(f"Rate limit exceeded. Retrying in 10 seconds... (Attempt {attempt + 1}/{retries})")
                time.sleep(10)  # Wait for 10 seconds before retrying
            else:
                break
    return None

async def generateChat(
    websocket,
    messages: List[Dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 2024,
) -> AsyncGenerator[str, None]:
    """Generate AI response with async streaming and text-to-speech conversion."""
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
                x=str(chunk.text)
                y=x.replace("*"," ")
                yield y  # Send the text to the user

                # Convert text to speech using smallest.ai
                audio_data = text_to_speech(y)  # Call the updated function

                if audio_data:
                    # Stream the audio file to the client
                    logging.debug(f"Generated audio data size: {len(audio_data)} bytes")

                    await websocket.send(audio_data)

                await asyncio.sleep(0.01)  # Small delay to prevent flooding

    except Exception as e:
        logging.error(f"Generation error: {str(e)}")
        yield f"Error: {str(e)}"

async def handle_messageChat(websocket, user_message: str) -> str:
    """Process a single message and return the full response."""
    # Add user message to conversation history
    client_manager.conversation_manager.add_message(websocket, "user", user_message)

    # Get full conversation history
    conversation_history = client_manager.conversation_manager.get_conversation(websocket)

    # Generate and collect the full response
    full_response = ""
    async for response_chunk in generateChat(websocket,conversation_history):
        if response_chunk:
            full_response += response_chunk
            await websocket.send(response_chunk)
            await asyncio.sleep(0.1)  # Prevent flooding

    return full_response

async def handle_clientChat(websocket):
    """Handle individual client connections with conversation context."""
    remote_address = websocket.remote_address if hasattr(websocket, "remote_address") else "unknown"

    try:
        client_manager.add_client(websocket)
        logging.info(f"New client connected from {remote_address}")

        # Initialize the conversation with the prompt
        client_manager.conversation_manager.initialize_conversation(websocket)

        # Send the greeting message
        await websocket.send(GREETING)

        async for message in websocket:
            try:
                data = json.loads(message)
                if "content" not in data:
                    await websocket.send("Error: Message must contain 'content' field")
                    continue

                user_message = data["content"]
                if "confirm sale" in user_message.lower():
                    # Ask for name and phone number
                    await websocket.send("Great! Please provide your name and phone number.")
                    data = await websocket.recv()
                    details = json.loads(data)
                    name = details.get("name")
                    phone = details.get("phone")
                    if name and phone:
                        conversation = client_manager.conversation_manager.get_conversation(websocket)
                        client_manager.conversation_manager.save_sale(name, phone, conversation)
                        await websocket.send("Thank you! The sale has been confirmed.")
                    else:
                        await websocket.send("Error: Missing name or phone number.")
                else:
                    full_response = await handle_messageChat(websocket, user_message)
                    if full_response:
                        client_manager.conversation_manager.add_message(websocket, "assistant", full_response)

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

async def handle_message(websocket, user_message: str) -> str:
    client_manager.conversation_manager.add_chat_message(websocket, "user", user_message)
    conversation_history = client_manager.conversation_manager.get_chat_conversation(websocket)

    full_response = ""
    async for response_chunk in generateChat(websocket,conversation_history):
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

# Create Flask app for Twilio webhooks
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
        # Start WebSocket server
        # server = await websockets.serve(
        #     handle_client,
        #     "127.0.0.1",
        #     5000,
        #     ping_interval=20,
        #     ping_timeout=10,
        # )
        websocket_server = await websockets.serve(
            handle_client,
            "127.0.0.1",
            5000,
            ping_interval=20,
            ping_timeout=10,
        )
        logging.info("WebSocket server started at ws://127.0.0.1:5000")

        # Start aiohttp server for Twilio webhooks
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 5001)
        await site.start()
        logging.info("Twilio webhook server started at http://127.0.0.1:5001")

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
