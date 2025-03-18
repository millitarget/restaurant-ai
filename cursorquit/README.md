<a href="https://livekit.io/">
  <img src="./.github/assets/livekit-mark.png" alt="LiveKit logo" width="100" height="100">
</a>

# Python Voice Agent

<p>
  <a href="https://cloud.livekit.io/projects/p_/sandbox"><strong>Deploy a sandbox app</strong></a>
  •
  <a href="https://docs.livekit.io/agents/overview/">LiveKit Agents Docs</a>
  •
  <a href="https://livekit.io/cloud">LiveKit Cloud</a>
  •
  <a href="https://blog.livekit.io/">Blog</a>
</p>

# Portuguese Restaurant Voice Assistant

This project implements a voice assistant for a Portuguese restaurant using the LiveKit Agents framework. The assistant can handle phone calls, process DTMF inputs, and respond to voice commands in Portuguese.

## Features

- **Portuguese Language Support**: Uses Deepgram for STT and ElevenLabs for TTS with Portuguese language settings
- **DTMF Input Handling**: Supports touch-tone inputs for menu navigation
- **Voice Command Recognition**: Detects keywords like "reservar" to trigger specific workflows
- **Restaurant Menu**: Provides information about the restaurant's menu in Portuguese
- **Reservation System**: Helps customers make reservations by collecting date, time, and party size

## Prerequisites

- Python 3.8+
- LiveKit account with API key and secret
- Deepgram API key for speech-to-text
- ElevenLabs API key for text-to-speech
- OpenAI API key for language model

## Setup

1. Clone this repository
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Create a `.env.local` file with your API keys:
   ```
   LIVEKIT_URL=<your LiveKit server URL>
   LIVEKIT_API_KEY=<your API Key>
   LIVEKIT_API_SECRET=<your API Secret>
   OPENAI_API_KEY=<your OpenAI API Key>
   DEEPGRAM_API_KEY=<your Deepgram API Key>
   ELEVENLABS_API_KEY=<your ElevenLabs API Key>
   ```

## Running the Assistant

Run the application with:

```
python agent.py
```

This will start the voice assistant and connect it to a LiveKit room. Once a participant joins, the assistant will greet them and be ready to handle their queries.

## Testing in Sandbox

The assistant is designed to work in a LiveKit sandbox environment for testing purposes. To test:

1. Start the assistant with `python agent.py`
2. Join the LiveKit room from a browser or client application
3. Test DTMF inputs by pressing 1, 2, or 3 on your keypad
4. Test voice commands by saying phrases including "reservar"

## License

See the LICENSE file for details.

## Dev Setup

Clone the repository and install dependencies to a virtual environment:

```console
# Linux/macOS
cd voice-pipeline-agent-python
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 agent.py download-files
```

<details>
  <summary>Windows instructions (click to expand)</summary>
  
```cmd
:: Windows (CMD/PowerShell)
cd voice-pipeline-agent-python
python3 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```
</details>


Set up the environment by copying `.env.example` to `.env.local` and filling in the required values:

- `LIVEKIT_URL`
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`
- `OPENAI_API_KEY`
- `CARTESIA_API_KEY`
- `DEEPGRAM_API_KEY`

You can also do this automatically using the LiveKit CLI:

```console
lk app env
```

Run the agent:

```console
python3 agent.py dev
```

This agent requires a frontend application to communicate with. You can use one of our example frontends in [livekit-examples](https://github.com/livekit-examples/), create your own following one of our [client quickstarts](https://docs.livekit.io/realtime/quickstarts/), or test instantly against one of our hosted [Sandbox](https://cloud.livekit.io/projects/p_/sandbox) frontends.
