"""Serveur d'assistant vocal local.

Les modules de ce paquet sont ajoutés étape par étape :

    étape 4  : MqttLink, SessionManager, AudioBuffer
    étape 5  : AudioResampler, AudioSender (mode écho)
    étape 6  : SpeechToText, TextToSpeech, Assistant, VoicePipeline
    étape 14 : AccessPolicy (droits par personne)
    F2       : FingerprintBook (emplacement du capteur → nom)

"""

__version__ = "0.1.0"
