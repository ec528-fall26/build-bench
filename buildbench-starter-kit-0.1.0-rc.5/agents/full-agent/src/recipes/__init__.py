"""Recipes: recognise a known failure signature, then write the verified edit.

See registry.py. Importing this package registers nothing by itself (patterns.py
imports it lazily, and the recipe modules import patterns); call
``registry.load()`` or any registry function and every module is loaded.
"""
