"""SMDA BackendInterface over a Binary Ninja BinaryView.

Pass an instance as ``ida_interface`` to ``smda.ida.IdaExporter.IdaExporter``.
Binary Ninja imports are lazy so unit tests can drive the interface with fakes.
"""

from __future__ import annotations

import re

_SYNTHETIC_SECTIONS = {".extern", ".synthetic_builtins"}


class BinjaSmdaInterface:
    def __init__(self, bv):
        self.bv = bv
        self._code_refs_from = None
        self._code_refs_to = None
        self._block_addresses: dict[tuple[int, int], list[int]] = {}
        self._instruction_bytes: dict[int, bytes] = {}

    def getArchitecture(self):
        from mcrit_similarity.architectures import smda_architecture

        return smda_architecture(self.bv.arch.name)

    def getBitness(self):
        return self.bv.arch.address_size * 8

    def getFunctions(self):
        return sorted(function.start for function in self.bv.functions)

    def _instruction_addresses(self, block):
        # Iterating a BasicBlock renders disassembly text per instruction, which dominated export
        # time. Decode lengths only, once per block, and keep the bytes for getInstructionBytes.
        key = (block.start, block.end)
        cached = self._block_addresses.get(key)
        if cached is not None:
            return cached
        data = self.bv.read(block.start, block.end - block.start)
        addresses = []
        offset = 0
        while offset < len(data):
            address = block.start + offset
            length = self.bv.get_instruction_length(address, block.arch)
            if length == 0:
                break
            addresses.append(address)
            self._instruction_bytes[address] = data[offset : offset + length]
            offset += length
        self._block_addresses[key] = addresses
        return addresses

    def getBlocks(self, function_offset):
        function = self.bv.get_function_at(function_offset)
        if function is None:
            return []
        blocks = [self._instruction_addresses(block) for block in function.basic_blocks]
        return sorted(block for block in blocks if block)

    def getInstructionBytes(self, offset):
        cached = self._instruction_bytes.get(offset)
        if cached is not None:
            return cached
        length = self.bv.get_instruction_length(offset)
        return self.bv.read(offset, length) if length else b""

    def _build_code_refs(self):
        refs_from = {}
        for function in self.bv.functions:
            call_sites = {reference.address for reference in function.call_sites}
            for block in function.basic_blocks:
                addresses = self._instruction_addresses(block)
                for index, address in enumerate(addresses):
                    targets = set()
                    if address in call_sites:
                        targets.update(self.bv.get_callees(address, func=function))
                    if index + 1 < len(addresses):
                        targets.add(addresses[index + 1])
                    else:
                        targets.update(edge.target.start for edge in block.outgoing_edges)
                    refs_from.setdefault(address, set()).update(targets)
        refs_to = {}
        for source, targets in refs_from.items():
            for target in targets:
                refs_to.setdefault(target, set()).add(source)
        self._code_refs_from = refs_from
        self._code_refs_to = refs_to

    def getCodeInRefs(self, offset):
        if self._code_refs_to is None:
            self._build_code_refs()
        refs_to = self._code_refs_to or {}
        return [(source, offset) for source in sorted(refs_to.get(offset, ()))]

    def getCodeOutRefs(self, offset):
        if self._code_refs_from is None:
            self._build_code_refs()
        refs_from = self._code_refs_from or {}
        return [(offset, target) for target in sorted(refs_from.get(offset, ()))]

    def _function_name(self, function, demangle):
        if not demangle:
            return function.name
        try:
            from binaryninja import DemanglerConfig, demangle_any

            result = demangle_any(
                function.symbol.raw_name, DemanglerConfig.for_binary_view(self.bv)
            )
            return str(result.name) if result else function.name
        except Exception:
            return function.name

    def getFunctionSymbols(self, demangle=False):
        symbols = {}
        for function in self.bv.functions:
            name = self._function_name(function, demangle)
            if name and not re.match(r"sub_[0-9a-fA-F]+$", name):
                symbols[function.start] = name
        return symbols

    def _data_segments(self):
        return sorted(
            (segment for segment in self.bv.segments if segment.data_length),
            key=lambda segment: segment.start,
        )

    def getBaseAddr(self):
        segments = self._data_segments()
        if not segments:
            return 0
        return (segments[0].start // 0x10000) * 0x10000

    def getBinary(self):
        segments = self._data_segments()
        if not segments:
            return b""
        base = self.getBaseAddr()
        image = bytearray(max(segment.end for segment in segments) - base)
        for segment in segments:
            data = self.bv.read(segment.start, segment.data_length)
            image[segment.start - base : segment.start - base + len(data)] = data
        return bytes(image)

    def getApiMap(self):
        try:
            from binaryninja.enums import SymbolType

            types = (SymbolType.ImportAddressSymbol, SymbolType.ImportedFunctionSymbol)
        except Exception:
            return {}
        api_map = {}
        for symbol_type in types:
            for symbol in self.bv.get_symbols_of_type(symbol_type):
                module = str(symbol.namespace) if symbol.namespace else ""
                name = symbol.raw_name
                if module and module != "BNINTERNALNAMESPACE":
                    name = f"{module}!{name}"
                api_map[symbol.address] = name
        return api_map

    def getApiOffsets(self):
        return self.getApiMap()

    def isExternalFunction(self, function_offset):
        return any(
            section.name in _SYNTHETIC_SECTIONS
            for section in self.bv.get_sections_at(function_offset)
        )
