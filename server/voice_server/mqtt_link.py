"""Connexion du serveur au broker MQTT (paho-mqtt 2.x)."""

from __future__ import annotations

import json
import logging
import socket
import threading
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

from voice_server.settings import MqttSettings

logger = logging.getLogger(__name__)

# Rappel : (identifiant de la carte, payload brut)
Handler = Callable[[str, bytes], Any]
# Rappel d'un topic hors voice/ (agents Linux, étape 15b) : (topic complet, payload brut)
TopicHandler = Callable[[str, bytes], Any]

ONLINE = json.dumps({"status": "online"}).encode()
OFFLINE = json.dumps({"status": "offline"}).encode()
REFUSED_CREDENTIALS = (134, 135)  # « Bad user name or password », « Not authorized » (MQTT 5 / 3.1.1)
STOP_PUBLISH_TIMEOUT_S = 2.0


def presence_topic(prefix: str) -> str:
    """Topic de présence du serveur, lu par les cartes (étape 15).

    Hors du motif <préfixe>/<carte>/<suffixe> auquel le serveur s'abonne
    (voice/+/state...) : sinon il se prendrait lui-même pour une carte.

    >>> presence_topic("voice")
    'voice/server/status'
    """
    return f"{prefix}/server/status"


def refusal_hint(reason: ReasonCode, client_id: str) -> str:
    """Explication d'une connexion refusée, pour le journal."""
    if reason.value in REFUSED_CREDENTIALS:
        return (f"connexion refusee par le broker : {reason}. Verifiez MQTT_USERNAME et MQTT_PASSWORD "
                "dans server/.env, et que ce compte existe (make mqtt-user NAME=voice-server, "
                "puis make mosquitto-docker)")
    return f"connexion refusee par le broker : {reason} (client « {client_id} »)"


def split_topic(topic: str, prefix: str) -> tuple[str, str] | None:
    """Découpe « <préfixe>/<carte>/<suffixe> » en (carte, suffixe), sinon None.

    >>> split_topic("voice/esp32-01/audio/in", "voice")
    ('esp32-01', 'audio/in')
    """
    parts = topic.split("/", 2)
    if len(parts) != 3 or parts[0] != prefix or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


class MqttLink:
    """Abonne le serveur aux topics de toutes les cartes et répartit les messages.

    Le réseau tourne dans un fil créé par paho (loop_start) : les rappels sont
    donc appelés depuis ce fil. Reconnexion automatique, abonnements refaits à
    chaque connexion.

    Avec presence=True (le vrai serveur seulement, jamais un outil de test),
    le lien publie « online » retenu sur voice/server/status et confie au broker
    un testament « offline » : si le serveur meurt, les cartes le savent et
    coupent leur micro.
    """

    def __init__(self, settings: MqttSettings, presence: bool = False) -> None:
        """
        Args:
            presence: annonce la présence du serveur (étape 15) ; False pour les outils,
                dont l'arrêt ne doit pas faire croire aux cartes que le serveur est parti.
        """
        self._settings = settings
        self._presence = presence_topic(settings.topic_prefix) if presence else None
        self._handlers: dict[str, Handler] = {}
        self._topic_handlers: dict[str, TopicHandler] = {}
        self._connected = threading.Event()
        self._client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=settings.client_id,
        )
        if settings.username:
            self._client.username_pw_set(settings.username, settings.password)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.on_socket_open = self._on_socket_open
        self._client.reconnect_delay_set(min_delay=1, max_delay=10)
        if self._presence is not None:
            # Testament : publié PAR LE BROKER si le serveur disparaît sans prévenir
            # (plantage, coupure) ; retenu, pour une carte qui se connecte plus tard.
            self._client.will_set(self._presence, OFFLINE, qos=1, retain=True)

    def on(self, suffix: str, handler: Handler) -> None:
        """Associe un rappel à un suffixe : « event », « audio/in », « state »..."""
        self._handlers[suffix] = handler

    def on_topic(self, pattern: str, handler: TopicHandler) -> None:
        """Associe un rappel à un filtre complet hors voice/ : « agent/+/state » (étape 15b), QoS 1."""
        self._topic_handlers[pattern] = handler

    def start(self) -> None:
        """Lance la connexion (asynchrone) et le fil réseau."""
        logger.info("connexion au broker %s:%d", self._settings.host, self._settings.port)
        self._client.connect_async(self._settings.host, self._settings.port,
                                   keepalive=self._settings.keepalive)
        self._client.loop_start()

    def wait_connected(self, timeout: float) -> bool:
        """Attend la première connexion au broker ; False si le délai expire."""
        return self._connected.wait(timeout)

    def publish(self, device: str, suffix: str, payload: bytes, qos: int) -> None:
        """Publie vers une carte : <préfixe>/<carte>/<suffixe>. Utilisable depuis tout fil."""
        topic = f"{self._settings.topic_prefix}/{device}/{suffix}"
        self._client.publish(topic, payload, qos=qos)

    def publish_topic(self, topic: str, payload: bytes, qos: int) -> None:
        """Publie sur un topic complet, hors de voice/ : commandes domotiques home/... (étape 11)."""
        self._client.publish(topic, payload, qos=qos)

    def stop(self) -> None:
        """Déconnexion propre et arrêt du fil réseau."""
        if self._presence is not None and self._connected.is_set():
            # Une déconnexion propre ne déclenche PAS le testament : on annonce le départ.
            info = self._client.publish(self._presence, OFFLINE, qos=1, retain=True)
            try:
                info.wait_for_publish(STOP_PUBLISH_TIMEOUT_S)  # ne lève rien si le délai expire
            except (RuntimeError, ValueError):
                pass
            if not info.is_published():
                logger.warning("depart du serveur non annonce aux cartes (broker injoignable)")
        self._client.disconnect()
        self._client.loop_stop()

    # ------------------------------------------------------------ rappels paho

    def _on_connect(self, client: mqtt.Client, userdata: Any, flags: mqtt.ConnectFlags,
                    reason_code: ReasonCode, properties: Properties | None) -> None:
        if reason_code.is_failure:
            logger.error("%s", refusal_hint(reason_code, self._settings.client_id))
            return
        logger.info("connecte au broker")
        self._connected.set()
        if self._presence is not None:
            # À chaque connexion : un broker redémarré (sans persistance) a oublié l'état retenu.
            client.publish(self._presence, ONLINE, qos=1, retain=True)
            logger.info("presence annoncee sur %s", self._presence)
        # Abonnement refait à chaque connexion : une reconnexion repart de zéro.
        for suffix in self._handlers:
            topic = f"{self._settings.topic_prefix}/+/{suffix}"
            qos = 0 if suffix.startswith("audio") else 1
            client.subscribe(topic, qos=qos)
            logger.info("abonne a %s (QoS %d)", topic, qos)
        for pattern in self._topic_handlers:
            client.subscribe(pattern, qos=1)
            logger.info("abonne a %s (QoS 1)", pattern)

    def _on_socket_open(self, client: mqtt.Client, userdata: Any, sock: Any) -> None:
        # Étape 16 : sans cela, le noyau retient les petits paquets en attendant un accusé de
        # réception (algorithme de Nagle) ; un chunk audio de 1600 octets tient en deux paquets,
        # et le second attendrait. Un flux temps réel envoie tout de suite.
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except (OSError, AttributeError):  # socket TLS ou WebSocket enveloppé : option indisponible
            logger.debug("TCP_NODELAY non applique")

    def _on_disconnect(self, client: mqtt.Client, userdata: Any,
                       flags: mqtt.DisconnectFlags, reason_code: ReasonCode,
                       properties: Properties | None) -> None:
        self._connected.clear()
        if reason_code.is_failure:
            # Cause fréquente en TP : un second programme avec le même identifiant
            # client ; le broker n'en garde qu'un et les deux s'éjectent en boucle.
            logger.warning("connexion au broker perdue (%s), reconnexion... Si cela se repete, "
                           "un autre client utilise-t-il l'identifiant « %s » ?",
                           reason_code, self._settings.client_id)

    def _on_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        parsed = split_topic(message.topic, self._settings.topic_prefix)
        try:
            if parsed is not None:
                handler = self._handlers.get(parsed[1])
                if handler is not None:
                    handler(parsed[0], message.payload)
                return
            for pattern, topic_handler in self._topic_handlers.items():
                if mqtt.topic_matches_sub(pattern, message.topic):
                    topic_handler(message.topic, message.payload)
        except Exception:  # un message fautif ne doit jamais arrêter le serveur
            logger.exception("erreur en traitant %s", message.topic)
