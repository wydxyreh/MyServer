# -*- coding: utf-8 -*-
# Generated by the protocol buffer compiler.  DO NOT EDIT!
# source: sample.proto
"""Generated protocol buffer code."""
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf import reflection as _reflection
from google.protobuf import symbol_database as _symbol_database
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()




DESCRIPTOR = _descriptor.FileDescriptor(
  name='sample.proto',
  package='sample',
  syntax='proto2',
  serialized_options=None,
  create_key=_descriptor._internal_create_key,
  serialized_pb=b'\n\x0csample.proto\x12\x06sample\"3\n\rEntityMessage\x12\x10\n\x08\x66uncname\x18\x01 \x02(\t\x12\x10\n\x08\x66uncargs\x18\x02 \x01(\t\"2\n\x0cLoginMessage\x12\x10\n\x08username\x18\x01 \x02(\t\x12\x10\n\x08password\x18\x02 \x02(\t'
)




_ENTITYMESSAGE = _descriptor.Descriptor(
  name='EntityMessage',
  full_name='sample.EntityMessage',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  create_key=_descriptor._internal_create_key,
  fields=[
    _descriptor.FieldDescriptor(
      name='funcname', full_name='sample.EntityMessage.funcname', index=0,
      number=1, type=9, cpp_type=9, label=2,
      has_default_value=False, default_value=b"".decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
    _descriptor.FieldDescriptor(
      name='funcargs', full_name='sample.EntityMessage.funcargs', index=1,
      number=2, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=b"".decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
  ],
  extensions=[
  ],
  nested_types=[],
  enum_types=[
  ],
  serialized_options=None,
  is_extendable=False,
  syntax='proto2',
  extension_ranges=[],
  oneofs=[
  ],
  serialized_start=24,
  serialized_end=75,
)


_LOGINMESSAGE = _descriptor.Descriptor(
  name='LoginMessage',
  full_name='sample.LoginMessage',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  create_key=_descriptor._internal_create_key,
  fields=[
    _descriptor.FieldDescriptor(
      name='username', full_name='sample.LoginMessage.username', index=0,
      number=1, type=9, cpp_type=9, label=2,
      has_default_value=False, default_value=b"".decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
    _descriptor.FieldDescriptor(
      name='password', full_name='sample.LoginMessage.password', index=1,
      number=2, type=9, cpp_type=9, label=2,
      has_default_value=False, default_value=b"".decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
  ],
  extensions=[
  ],
  nested_types=[],
  enum_types=[
  ],
  serialized_options=None,
  is_extendable=False,
  syntax='proto2',
  extension_ranges=[],
  oneofs=[
  ],
  serialized_start=77,
  serialized_end=127,
)

DESCRIPTOR.message_types_by_name['EntityMessage'] = _ENTITYMESSAGE
DESCRIPTOR.message_types_by_name['LoginMessage'] = _LOGINMESSAGE
_sym_db.RegisterFileDescriptor(DESCRIPTOR)

EntityMessage = _reflection.GeneratedProtocolMessageType('EntityMessage', (_message.Message,), {
  'DESCRIPTOR' : _ENTITYMESSAGE,
  '__module__' : 'sample_pb2'
  # @@protoc_insertion_point(class_scope:sample.EntityMessage)
  })
_sym_db.RegisterMessage(EntityMessage)

LoginMessage = _reflection.GeneratedProtocolMessageType('LoginMessage', (_message.Message,), {
  'DESCRIPTOR' : _LOGINMESSAGE,
  '__module__' : 'sample_pb2'
  # @@protoc_insertion_point(class_scope:sample.LoginMessage)
  })
_sym_db.RegisterMessage(LoginMessage)


# @@protoc_insertion_point(module_scope)
