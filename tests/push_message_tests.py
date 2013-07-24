from nose.tools import *
import datetime
import logging
import json

from dbsync import models
from dbsync.messages.push import PushMessage

from tests.models import A, B, Session


def addstuff():
    a1 = A(name="first a")
    a2 = A(name="second a")
    b1 = B(name="first b", a=a1)
    b2 = B(name="second b", a=a1)
    b3 = B(name="third b", a=a2)
    session = Session()
    session.add_all([a1, a2, b1, b2, b3])
    session.commit()

def changestuff():
    session = Session()
    a1, a2 = session.query(A)
    b1, b2, b3 = session.query(B)
    a1.name = "first a modified"
    b2.a = a2
    session.delete(b3)
    session.commit()

def setup():
    pass

def teardown():
    session = Session()
    map(session.delete, session.query(A))
    map(session.delete, session.query(B))
    map(session.delete, session.query(models.Operation))
    session.commit()


@with_setup(setup, teardown)
def test_create_message():
    addstuff()
    changestuff()
    session = Session()
    message = PushMessage()
    message.node = session.query(models.Node).first()
    message.add_unversioned_operations()
    assert message.to_json() == PushMessage(message.to_json()).to_json()


@with_setup(setup, teardown)
def test_encode_message():
    addstuff()
    changestuff()
    session = Session()
    message = PushMessage()
    message.node = session.query(models.Node).first()
    message.add_unversioned_operations()
    assert message.to_json() == json.loads(json.dumps(message.to_json()))


@with_setup(setup, teardown)
def test_message_query():
    session = Session()
    message = PushMessage()
    message.node = session.query(models.Node).first()
    assert repr(message.query(models.Node).all()) == repr([message.node])
