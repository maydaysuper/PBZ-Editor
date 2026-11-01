"""Editable backpack snapshots with undo and a single explicit disk commit."""
from .core import EditorError


def item_key(item):
    return item['category'], item['id'].casefold()


def signature(item):
    if item is None:
        return None
    return (item.get('quantity', 1), item.get('level', 0),
            tuple(item.get('equipped_slots', [])))


class InventoryDraft:
    def __init__(self, session, slot, definitions=None):
        self.session, self.slot, self.view = session, slot, session
        self.definitions = {item_key(i): i for i in (definitions or [])}
        self.original = self._inventory(session)
        self.items = dict(self.original)
        self.history = []

    def _inventory(self, session):
        return {item_key(i): i for i in session.inventory(self.slot)}

    @property
    def changed_keys(self):
        return {key for key in self.original.keys() | self.items.keys()
                if signature(self.original.get(key)) != signature(self.items.get(key))}

    def stage(self, operation):
        view = self.view.project(self.slot, [dict(operation)])
        items = self._inventory(view)
        if {k: signature(v) for k, v in items.items()} == {k: signature(v) for k, v in self.items.items()}:
            return False
        self.history.append(self.view)
        self.history = self.history[-100:]
        self.view, self.items = view, items
        return True

    def undo(self):
        if self.history:
            self.view = self.history.pop()
            self.items = self._inventory(self.view)

    def discard(self):
        self.view, self.items, self.history = self.session, dict(self.original), []

    def operations(self):
        """Describe the final backpack, folding repeated edits into one target.

        Equipment changes precede removals so a replaced medicine may then be
        removed safely. Normalized counts are included in the final targets.
        """
        operations = []
        for key in sorted(self.changed_keys):
            before, after = self.original.get(key), self.items.get(key)
            item = after or before
            definition = self.definitions.get(key, {})
            op = {'id': item['id'], 'category': key[0],
                  'name': definition.get('name', item['id']),
                  'action': 'set' if after else 'remove'}
            if not after and key[0] == 'medicine' and before.get('equipped_slots'):
                operations.append({**op, 'action': 'set', 'quantity': 0,
                                   'level': before['level'], 'equip_slot': 'none'})
            if after:
                op.update(quantity=after['quantity'], level=after['level'])
                for field in ('max_level', 'base_capacity'):
                    if definition.get(field) is not None:
                        op[field] = definition[field]
                if key[0] == 'medicine':
                    slots = after.get('equipped_slots', [])
                    op['equip_slot'] = slots[0] if len(slots) == 1 else 'keep' if slots else 'none'
            operations.append(op)
        operations.sort(key=lambda op: (op['action'] == 'remove', op['category'] != 'medicine'))
        return operations

    def validated_operations(self):
        operations = self.operations()
        projected = self._inventory(self.session.project(self.slot, operations))
        if {k: signature(v) for k, v in projected.items()} != {k: signature(v) for k, v in self.items.items()}:
            raise EditorError('背包预览与保存结果不一致，已取消写入。请撤销最近的修改后重试。')
        return operations
