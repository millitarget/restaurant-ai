# Portuguese Restaurant Voice Assistant

A voice assistant for a Portuguese restaurant built with LiveKit, demonstrating real-time voice interactions for restaurant reservations and menu inquiries.

## Features

- Responds to customer queries in Portuguese
- Handles restaurant reservations
- Provides menu information
- Processes DTMF inputs for telephone-based interactions
- Uses ElevenLabs for natural-sounding Text-to-Speech
- Uses Deepgram for accurate Speech-to-Text in Portuguese

## Setup

1. Clone the repository
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Create a `.env.local` file with the following environment variables:
   ```
   DEEPGRAM_API_KEY=your_deepgram_api_key
   ELEVENLABS_API_KEY=your_elevenlabs_api_key
   OPENAI_API_KEY=your_openai_api_key
   LIVEKIT_API_KEY=your_livekit_api_key
   LIVEKIT_API_SECRET=your_livekit_api_secret
   LIVEKIT_URL=your_livekit_url
   ```

## Usage

To run the assistant and connect to a LiveKit room:

```
python agent.py connect --room YOUR_ROOM_NAME
```

## Dependencies

- LiveKit Agents SDK
- Deepgram API (for Speech-to-Text)
- ElevenLabs API (for Text-to-Speech)
- OpenAI API (for conversation)
- Silero VAD (for Voice Activity Detection)

## License

MIT 