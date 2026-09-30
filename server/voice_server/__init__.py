"""Serveur d'assistant vocal local.

Les modules de ce paquet sont ajoutés étape par étape :

    étape 4  : MqttLink, SessionManager, AudioBuffer
    étape 5  : AudioResampler, AudioSender (mode écho)
    étape 6  : SpeechToText, TextToSpeech, Assistant, VoicePipeline
    étape 7  : VoiceActivityDetector
    étape 8  : Diarizer
    étape 9  : SpeakerIdentifier

"""

__version__ = "0.1.0"
