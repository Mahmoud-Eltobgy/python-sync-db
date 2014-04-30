"""
Registry, pull, push and other request handlers.

The pull cycle consists in receiving a version identifier and sending
back a PullMessage filled with versions above the one received.

The push cycle consists in receiving a complete PushMessage and either
rejecting it based on latest version or signature, or accepting it and
performing the operations indicated in it. The operations should also
be inserted in the operations table, in the correct order but getting
new keys for the 'order' column, and linked with a newly created
version. If it accepts the message, the push handler should also
return the new version identifier to the node (and the programmer is
tasked to send the HTTP response).
"""

import datetime

from dbsync.lang import *
from dbsync.utils import (
    generate_secret,
    properties_dict,
    column_properties,
    query_model)
from dbsync import core
from dbsync.models import (
    Version,
    Node,
    ContentType,
    OperationError,
    Operation)
from dbsync.messages.base import BaseMessage
from dbsync.messages.register import RegisterMessage
from dbsync.messages.pull import PullMessage, PullRequestMessage
from dbsync.messages.push import PushMessage


def handle_query(data):
    """Responds to a query request."""
    model = core.synched_models.get(data.get('model', None), None)
    if model is None: return None
    mname = model.__name__
    filters = dict((k, v) for k, v in ((k[len(mname) + 1:], v)
                                       for k, v in data.iteritems()
                                       if k.startswith(mname + '_'))
                   if k and k in column_properties(model))
    session = core.Session()
    message = BaseMessage()
    q = query_model(session, model)
    if filters:
        q = q.filter_by(**filters)
    for obj in q:
        message.add_object(obj)
    session.close()
    return message.to_json()


def handle_repair():
    """Handle repair request. Return whole server database."""
    session = core.Session()
    latest_version_id = core.get_latest_version_id(session)
    message = BaseMessage()
    for model in core.synched_models.itervalues():
        for obj in query_model(session, model):
            message.add_object(obj)
    response = message.to_json()
    response['latest_version_id'] = latest_version_id
    session.close()
    return response


@core.with_listening(False)
@core.with_transaction
def handle_register(user_id=None, session=None):
    """Handle a registry request, creating a new node, wrapping it in
    a message and returning it to the client node.

    *user_id* can be a numeric key to a user record, which will be set
    in the node record itself."""
    newnode = Node()
    newnode.registered = datetime.datetime.now()
    newnode.registry_user_id = user_id
    newnode.secret = generate_secret(128)
    session.add(newnode)
    session.flush()
    message = RegisterMessage()
    message.node = newnode
    return message.to_json()


def handle_pull(data, extra_data=None):
    """Handle the pull request and return a dictionary object to be
    sent back to the node.

    *data* must be a dictionary-like object, usually one containing
    the GET parameters of the request.

    *extra_data* Additional information to be send back to client"""
    extra = dict((k, v) for k, v in extra_data.iteritems()
                 if k not in ('operations', 'created', 'payload', 'versions')) \
                 if extra_data is not None else {}

    session = core.Session()
    latest_version_id = data.get('latest_version_id', None)
    try:
        latest_version_id = int(latest_version_id)
    except (ValueError, TypeError):
        latest_version_id = None
    swell = not 'fast_forward' in data # allows for smaller messages if False
    versions = session.query(Version)
    if latest_version_id is not None:
        versions = versions.filter(Version.version_id > latest_version_id)
    message = PullMessage(extra_data=extra)
    for v in versions:
        message.add_version(v, swell=swell, session=session)
    session.close()
    return message.to_json()


class PullRejected(Exception): pass


def handle_pull_request(data, extra_data=None):
    """Handle the pull request and return a dictionary object to be
    sent back to the node.

    *data* must be a dictionary-like object, usually one obtained from
    decoding a JSON dictionary in the POST body.

    *extra_data* Additional information to be send back to client"""
    extra = dict((k, v) for k, v in extra_data.iteritems()
                 if k not in ('operations', 'created', 'payload', 'versions')) \
                 if extra_data is not None else {}

    try:
        request_message = PullRequestMessage(data)
    except KeyError:
        raise PullRejected("request object isn't a valid PullRequestMessage", data)

    message = PullMessage(extra_data=extra)
    message.fill_for(request_message)
    return message.to_json()


class PushRejected(Exception): pass


@core.with_listening(False)
@core.with_transaction
def handle_push(data, session=None):
    """Handle the push request and return a dictionary object to be
    sent back to the node.

    If the push is rejected, this procedure will raise a
    dbsync.server.handlers.PushRejected exception.

    *data* must be a dictionary-like object, usually the product of
    parsing a JSON string."""
    message = None
    try:
        message = PushMessage(data)
    except KeyError:
        raise PushRejected("request object isn't a valid PushMessage", data)
    latest_version_id = core.get_latest_version_id(session)
    if latest_version_id != message.latest_version_id:
        raise PushRejected("version identifier isn't the latest one; "\
                               "given: %d" % message.latest_version_id)
    if not message.operations:
        raise PushRejected("message doesn't contain operations")
    if not message.islegit(session):
        raise PushRejected("message isn't properly signed")
    # perform the operations
    try:
        content_types = session.query(ContentType).all()
        for op in message.operations:
            op.perform(content_types,
                       core.synched_models,
                       message,
                       session,
                       lambda s, errs: core.save_log(s, message.node_id, errs))
    except OperationError as e:
        core.save_log('handlers.push.OperationError', 
            message.node_id, [repr(arg) for arg in e.args])
        raise PushRejected("at least one operation couldn't be performed",
                           *e.args)
    # insert a new version
    version = Version(created=datetime.datetime.now(), node_id=message.node_id)
    session.add(version)
    # insert the operations, discarding the 'order' column
    for op in sorted(message.operations, key=attr('order')):
        new_op = Operation()
        for k in ifilter(lambda k: k != 'order', properties_dict(op)):
            setattr(new_op, k, getattr(op, k))
        session.add(new_op)
        new_op.version = version
        session.flush()
    # return the new version id back to the node
    return {'new_version_id': version.version_id}
