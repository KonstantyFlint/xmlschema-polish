#
# Copyright (c), 2016-2020, SISSA (International School for Advanced Studies).
# All rights reserved.
# This file is distributed under the terms of the MIT License.
# See the file 'LICENSE' in the root directory of the present
# distribution, or http://opensource.org/licenses/MIT.
#
# @author Davide Brunato <brunato@sissa.it>
#
"""
A unified setup module for ElementTree with a safe parser and helper functions.
"""
import sys
import importlib
import re
from collections import Counter

from .exceptions import XMLSchemaTypeError
from .namespaces import get_namespace
from .qnames import get_qname, get_prefixed_qname, XSI_SCHEMA_LOCATION, XSI_NONS_SCHEMA_LOCATION

_REGEX_NS_PREFIX = re.compile(r'ns\d+$')
_REGEX_SPACES = re.compile(r'\s+')

###
# Programmatic import of xml.etree.ElementTree
#
# In Python 3 the pure python implementation is overwritten by the C module API,
# so use a programmatic re-import to obtain the pure Python module, necessary for
# defining a safer XMLParser.
#
if '_elementtree' in sys.modules:
    # Temporary remove the loaded modules
    ElementTree = sys.modules.pop('xml.etree.ElementTree', None)
    _cmod = sys.modules.pop('_elementtree')

    # Load the pure Python module
    sys.modules['_elementtree'] = None
    PyElementTree = importlib.import_module('xml.etree.ElementTree')

    # Restore original modules
    sys.modules['_elementtree'] = _cmod
    if ElementTree is not None:
        sys.modules['xml.etree.ElementTree'] = ElementTree
    else:
        ElementTree = PyElementTree

else:
    # Load the pure Python module
    sys.modules['_elementtree'] = None
    PyElementTree = importlib.import_module('xml.etree.ElementTree')

    # Remove the pure Python module from imported modules
    del sys.modules['xml.etree.ElementTree']
    del sys.modules['_elementtree']

    # Load the C optimized ElementTree module
    ElementTree = importlib.import_module('xml.etree.ElementTree')


etree_element = ElementTree.Element
ParseError = ElementTree.ParseError
py_etree_element = PyElementTree.Element


class SafeXMLParser(PyElementTree.XMLParser):
    """
    An XMLParser that forbids entities processing. Drops the *html* argument that is deprecated
    since version 3.4.

    :param target: the target object called by the `feed()` method of the parser, \
    that defaults to `TreeBuilder`.
    :param encoding: if provided, its value overrides the encoding specified in the XML file.
    """
    def __init__(self, target=None, encoding=None):
        super(SafeXMLParser, self).__init__(target=target, encoding=encoding)
        parser = self.parser
        parser.EntityDeclHandler = self.entity_declaration
        parser.UnparsedEntityDeclHandler = self.unparsed_entity_declaration
        parser.ExternalEntityRefHandler = self.external_entity_reference

    def entity_declaration(self, entity_name, is_parameter_entity, value, base,
                           system_id, public_id, notation_name):
        raise PyElementTree.ParseError("Entities are forbidden "
                                       "(entity_name={!r})".format(entity_name))

    def unparsed_entity_declaration(self, entity_name, base, system_id, public_id, notation_name):
        raise PyElementTree.ParseError("Entities are forbidden "
                                       "(entity_name={!r})".format(entity_name))

    def external_entity_reference(self, context, base, system_id, public_id):
        raise PyElementTree.ParseError("External references are forbidden (system_id={!r}, "
                                       "public_id={!r})".format(system_id, public_id))


def is_etree_element(obj):
    """A checker for valid ElementTree elements that excludes XsdElement objects."""
    return hasattr(obj, 'append') and hasattr(obj, 'tag') and hasattr(obj, 'attrib')


def is_etree_document(obj):
    """A checker for valid ElementTree objects."""
    return hasattr(obj, 'getroot') and hasattr(obj, 'parse') and hasattr(obj, 'iter')


def etree_tostring(elem, namespaces=None, indent='', max_lines=None, spaces_for_tab=4,
                   xml_declaration=False, encoding='unicode', method='xml'):
    """
    Serialize an Element tree to a string. Tab characters are replaced by whitespaces.

    :param elem: the Element instance.
    :param namespaces: is an optional mapping from namespace prefix to URI. \
    Provided namespaces are registered before serialization.
    :param indent: the base line indentation.
    :param max_lines: if truncate serialization after a number of lines (default: do not truncate).
    :param spaces_for_tab: number of spaces for replacing tab characters (default is 4).
    :param xml_declaration: if set to `True` inserts the XML declaration at the head.
    :param encoding: if "unicode" (the default) the output is a string, otherwise it’s binary.
    :param method: is either "xml" (the default), "html" or "text".
    :return: a Unicode string.
    """
    def reindent(line):
        if not line:
            return line
        elif line.startswith(min_indent):
            return line[start:] if start >= 0 else indent[start:] + line
        else:
            return indent + line

    if not is_etree_element(elem):
        raise XMLSchemaTypeError("{!r} is not an Element".format(elem))

    elif isinstance(elem, py_etree_element):
        etree_module = PyElementTree
    elif not hasattr(elem, 'nsmap'):
        etree_module = ElementTree
    else:
        try:
            import lxml.etree as etree_module
        except ImportError:
            msg = "cannot serialize %r: lxml library is not available"
            raise XMLSchemaTypeError(msg % type(elem))

    if namespaces:
        default_namespace = namespaces.get('')
        for prefix, uri in namespaces.items():
            if prefix and not _REGEX_NS_PREFIX.match(prefix):
                etree_module.register_namespace(prefix, uri)
                if uri == default_namespace:
                    default_namespace = None

        if default_namespace:
            etree_module.register_namespace('', default_namespace)

    xml_text = etree_module.tostring(elem, encoding=encoding, method=method)

    lines = ['<?xml version="1.0" encoding="UTF-8"?>'] if xml_declaration else []
    lines.extend(xml_text.replace('\t', ' ' * spaces_for_tab).splitlines())
    while lines and not lines[-1].strip():
        lines.pop(-1)

    last_indent = ' ' * min(k for k in range(len(lines[-1])) if lines[-1][k] != ' ')
    if len(lines) > 2:
        child_indent = ' ' * min(
            k for line in lines[1:-1] for k in range(len(line)) if line[k] != ' '
        )
        min_indent = min(child_indent, last_indent)
    else:
        min_indent = child_indent = last_indent

    start = len(min_indent) - len(indent)

    if max_lines is not None and len(lines) > max_lines + 2:
        lines = lines[:max_lines] + [child_indent + '...'] * 2 + lines[-1:]

    return '\n'.join(reindent(line) for line in lines)


def etree_iterpath(elem, tag=None, path='.', namespaces=None, add_position=False):
    """
    Creates an iterator for the element and its subelements that yield elements and paths.
    If tag is not `None` or '*', only elements whose matches tag are returned from the iterator.

    :param elem: the element to iterate.
    :param tag: tag filtering.
    :param path: the current path, '.' for default.
    :param namespaces: is an optional mapping from namespace prefix to URI.
    :param add_position: add context position to child elements that appear multiple times.
    """
    if tag == "*":
        tag = None
    if tag is None or elem.tag == tag:
        yield elem, path

    if add_position:
        children_tags = Counter([e.tag for e in elem])
        positions = Counter([t for t in children_tags if children_tags[t] > 1])
    else:
        positions = ()

    for child in elem:
        if callable(child.tag):
            continue  # Skip lxml comments

        child_name = child.tag if namespaces is None else get_prefixed_qname(child.tag, namespaces)
        if path == '/':
            child_path = '/%s' % child_name
        elif path:
            child_path = '/'.join((path, child_name))
        else:
            child_path = child_name

        if child.tag in positions:
            child_path += '[%d]' % positions[child.tag]
            positions[child.tag] += 1

        yield from etree_iterpath(child, tag, child_path, namespaces, add_position)


def etree_getpath(elem, root, namespaces=None, relative=True,
                  add_position=False, parent_path=False):
    """
    Returns the XPath path from *root* to descendant *elem* element.

    :param elem: the descendant element.
    :param root: the root element.
    :param namespaces: is an optional mapping from namespace prefix to URI.
    :param relative: returns a relative path.
    :param add_position: add context position to child elements that appear multiple times.
    :param parent_path: if set to `True` returns the parent path. Default is `False`.
    :return: An XPath expression or `None` if *elem* is not a descendant of *root*.
    """
    if relative:
        path = '.'
    elif namespaces:
        path = '/%s' % get_prefixed_qname(root.tag, namespaces)
    else:
        path = '/%s' % root.tag

    if not parent_path:
        for e, path in etree_iterpath(root, elem.tag, path, namespaces, add_position):
            if e is elem:
                return path
    elif elem in root:
        return path
    else:
        for e, path in etree_iterpath(root, elem.tag, path, namespaces, add_position):
            if elem in e:
                return path


def etree_iter_location_hints(elem):
    """Yields schema location hints contained in the attributes of an element."""
    if XSI_SCHEMA_LOCATION in elem.attrib:
        locations = elem.attrib[XSI_SCHEMA_LOCATION].split()
        for ns, url in zip(locations[0::2], locations[1::2]):
            yield ns, url

    if XSI_NONS_SCHEMA_LOCATION in elem.attrib:
        for url in elem.attrib[XSI_NONS_SCHEMA_LOCATION].split():
            yield '', url


# noinspection PyUnresolvedReferences
def etree_elements_assert_equal(elem, other, strict=True, skip_comments=True, unordered=False):
    """
    Tests the equality of two XML Element trees.

    :param elem: the master Element tree, reference for namespace mapping.
    :param other: the other Element tree that has to be compared.
    :param strict: asserts strictly equality. `True` for default.
    :param skip_comments: skip comments from comparison.
    :param unordered: children may have different order.
    :raise: an AssertionError containing information about first difference encountered.
    """
    if unordered:
        children = sorted(elem, key=lambda x: '' if callable(x.tag) else x.tag)
        other_children = iter(sorted(
            other, key=lambda x: '' if callable(x.tag) else x.tag
        ))
    else:
        children = elem
        other_children = iter(other)

    namespace = ''
    for e1 in children:
        if skip_comments and callable(e1.tag):
            continue

        try:
            e2 = next(other_children)
        except StopIteration:
            assert False, "Node %r has more children than %r." % (elem, other)

        if strict or e1 is elem:
            assert e1.tag == e2.tag, "%r != %r: tags differ." % (e1, e2)
        else:
            namespace = get_namespace(e1.tag) or namespace
            assert get_qname(namespace, e1.tag) == get_qname(namespace, e2.tag), \
                "%r != %r: tags differ." % (e1, e2)

        # Attributes
        if e1.attrib != e2.attrib:
            if strict:
                msg = "{!r} != {!r}: attribute differ: {!r} != {!r}."
                raise AssertionError(msg % (e1, e2, e1.attrib, e2.attrib))
            else:
                msg = "%r != %r: attribute keys differ: %r != %r."
                assert sorted(e1.attrib.keys()) == sorted(e2.attrib.keys()), \
                    msg % (e1, e2, e1.attrib.keys(), e2.attrib.keys())
                for k in e1.attrib:
                    a1, a2 = e1.attrib[k].strip(), e2.attrib[k].strip()
                    if a1 != a2:
                        try:
                            assert float(a1) == float(a2)
                        except (AssertionError, ValueError, TypeError):
                            msg = "%r != %r: attribute %r differ: %r != %r."
                            raise AssertionError(msg % (e1, e2, k, a1, a2))

        # Number of children
        if skip_comments:
            nc1 = len([c for c in e1 if not callable(c.tag)])
            nc2 = len([c for c in e2 if not callable(c.tag)])
        else:
            nc1 = len(e1)
            nc2 = len(e2)
        assert nc1 == nc2, "%r != %r: children number differ: %r != %r." % (e1, e2, nc1, nc2)

        # Text
        if e1.text != e2.text:
            message = "%r != %r: texts differ: %r != %r." % (e1, e2, e1.text, e2.text)
            if strict:
                raise AssertionError(message)
            elif e1.text is None:
                assert not e2.text.strip(), message
            elif e2.text is None:
                assert not e1.text.strip(), message
            elif _REGEX_SPACES.sub(e1.text.strip(), '') != _REGEX_SPACES.sub(e2.text.strip(), ''):
                try:
                    assert float(e1.text.strip()) == float(e2.text.strip())
                except (AssertionError, ValueError, TypeError):
                    raise AssertionError(message)

        # Tail
        if e1.tail != e2.tail:
            message = "%r != %r: tails differ: %r != %r." % (e1, e2, e1.tail, e2.tail)
            if strict:
                raise AssertionError(message)
            elif e1.tail is None:
                assert not e2.tail.strip(), message
            elif e2.text is None:
                assert not e1.tail.strip(), message
            else:
                assert e1.tail.strip() == e2.tail.strip(), message

        etree_elements_assert_equal(e1, e2, strict, skip_comments, unordered)

    try:
        next(other_children)
    except StopIteration:
        pass
    else:
        assert False, "Node %r has lesser children than %r." % (elem, other)


def prune_etree(root, selector):
    """
    Removes from an tree structure the elements that verify the selector
    function. The checking and eventual removals are performed using a
    breadth-first visit method.

    :param root: the root element of the tree.
    :param selector: the single argument function to apply on each visited node.
    :return: `True` if the root node verify the selector function, `None` otherwise.
    """
    def _prune_subtree(elem):
        for child in elem[:]:
            if selector(child):
                elem.remove(child)

        for child in elem:
            _prune_subtree(child)

    if selector(root):
        del root[:]
        return True
    _prune_subtree(root)
