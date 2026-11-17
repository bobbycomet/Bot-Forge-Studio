"""The node editor: GraphScene (items + interaction) and NodeView (zoom/pan/menus/drops)."""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen,
                         QPainterPathStroker, QAction)
from PyQt6.QtWidgets import (QApplication, QGraphicsItem, QGraphicsPathItem, QGraphicsScene, QGraphicsView,
                             QMenu)

from ..core.model import CATEGORY_COLORS, Edge, Graph, Node, all_specs
from . import theme

NODE_W = 232
HEADER_H = 28
ROW_H = 22
PORT_R = 6
MIME = "application/x-botstudio-node"


def port_color(kind: str, dtype: str) -> QColor:
    return QColor(theme.PORT_COLORS.get("exec" if kind == "exec" else dtype, theme.PORT_COLORS["any"]))


def summary_of(node: Node) -> str:
    spec = node.spec
    if spec is None:
        return ""
    if spec.type_id == "trigger.slash":
        return "/" + str(node.props.get("name", ""))
    for p in spec.props:
        v = node.props.get(p.key)
        if p.key.endswith(("color", "footer")):
            continue
        if p.kind in ("str", "text", "choice", "any") and str(v or "").strip():
            s = str(v).replace("\n", " ")
            return s if len(s) <= 34 else s[:33] + "…"
    return ""


class PortItem(QGraphicsItem):
    def __init__(self, node_item: "NodeItem", name: str, kind: str, dtype: str, output: bool, index: int):
        super().__init__(node_item)
        self.node_item, self.name, self.kind, self.dtype, self.output = node_item, name, kind, dtype, output
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.hover = False
        y = HEADER_H + 8 + index * ROW_H + ROW_H / 2
        self.setPos(NODE_W if output else 0, y)
        self.setZValue(2)

    def boundingRect(self) -> QRectF:
        return QRectF(-PORT_R - 5, -PORT_R - 5, 2 * PORT_R + 10, 2 * PORT_R + 10)

    def paint(self, p: QPainter, opt, widget=None):
        c = port_color(self.kind, self.dtype)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(theme.BG), 2))
        p.setBrush(QBrush(c.lighter(130) if self.hover else c))
        if self.kind == "exec":
            p.drawPolygon([QPointF(-6, -6), QPointF(3, -6), QPointF(8, 0), QPointF(3, 6), QPointF(-6, 6)]
                          if self.output else
                          [QPointF(-8, -6), QPointF(1, -6), QPointF(6, 0), QPointF(1, 6), QPointF(-8, 6)])
        else:
            p.drawEllipse(QPointF(0, 0), PORT_R - 1, PORT_R - 1)

    def hoverEnterEvent(self, e):
        self.hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self.hover = False
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.scene().begin_connect(self, e.scenePos())
            e.accept()
        else:
            e.ignore()


class NodeItem(QGraphicsItem):
    def __init__(self, node: Node):
        super().__init__()
        self.node = node
        self.ports: list[PortItem] = []
        self.edges: list["EdgeItem"] = []
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setPos(node.x, node.y)
        self.setZValue(1)
        self._h = 60
        self.relayout()

    # -- layout -------------------------------------------------------------------------
    def relayout(self):
        for p in self.ports:
            if p.scene():
                p.scene().removeItem(p)
        self.ports = []
        self.prepareGeometryChange()
        ins, outs = self.node.ports()
        for i, pt in enumerate(ins):
            self.ports.append(PortItem(self, pt.name, pt.kind, pt.dtype, False, i))
        for i, pt in enumerate(outs):
            self.ports.append(PortItem(self, pt.name, pt.kind, pt.dtype, True, i))
        rows = max(len(ins), len(outs), 1)
        self._summary = summary_of(self.node)
        self._h = HEADER_H + 14 + rows * ROW_H + (20 if self._summary else 0)
        self.update()

    def refresh_summary(self):
        self.prepareGeometryChange()
        self._summary = summary_of(self.node)
        rows = max(len(self.node.ports()[0]), len(self.node.ports()[1]), 1)
        self._h = HEADER_H + 14 + rows * ROW_H + (20 if self._summary else 0)
        self.update()

    def boundingRect(self) -> QRectF:
        return QRectF(-4, -4, NODE_W + 8, self._h + 8)

    def port_pos(self, port: PortItem) -> QPointF:
        return port.scenePos()

    def find_port(self, name: str, output: bool) -> PortItem | None:
        return next((p for p in self.ports if p.name == name and p.output == output), None)

    # -- painting ---------------------------------------------------------------------------
    def paint(self, p: QPainter, opt, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        spec = self.node.spec
        sel = self.isSelected()
        body = QPainterPath()
        body.addRoundedRect(QRectF(0, 0, NODE_W, self._h), 7, 7)
        p.setPen(QPen(QColor(theme.ACCENT) if sel else QColor("#0b1017"), 2 if sel else 1))
        p.setBrush(QColor("#223447"))
        p.drawPath(body)

        if spec is None:
            hc, title = QColor(theme.BAD), f"Missing: {self.node.type_id}"
        else:
            hc, title = QColor(CATEGORY_COLORS.get(spec.category, "#8f98a0")), spec.title
        head = QPainterPath()
        head.addRoundedRect(QRectF(0, 0, NODE_W, HEADER_H), 7, 7)
        head.addRect(QRectF(0, HEADER_H - 8, NODE_W, 8))
        p.save()
        p.setClipPath(body)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(hc.darker(150))
        p.drawPath(head)
        p.setBrush(hc)
        p.drawRect(QRectF(0, 0, 5, HEADER_H))
        p.restore()

        f = QFont(p.font())
        f.setBold(True)
        f.setPointSizeF(9.5)
        p.setFont(f)
        p.setPen(QColor("white"))
        p.drawText(QRectF(14, 0, NODE_W - 20, HEADER_H), Qt.AlignmentFlag.AlignVCenter, title)

        f.setBold(False)
        f.setPointSizeF(9)
        p.setFont(f)
        fm = QFontMetrics(f)
        ins = [x for x in self.ports if not x.output]
        outs = [x for x in self.ports if x.output]
        for i, it in enumerate(ins):
            pt = self.node.port(it.name, False)
            wired = self._wired(it.name)
            p.setPen(QColor(theme.ACCENT) if wired else QColor(theme.TEXT))
            y = HEADER_H + 8 + i * ROW_H
            p.drawText(QRectF(12, y, NODE_W / 2 - 14, ROW_H), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(pt.text if pt else it.name, Qt.TextElideMode.ElideRight, int(NODE_W / 2 - 16)))
        for i, it in enumerate(outs):
            pt = self.node.port(it.name, True)
            p.setPen(QColor(theme.TEXT))
            y = HEADER_H + 8 + i * ROW_H
            p.drawText(QRectF(NODE_W / 2, y, NODE_W / 2 - 12, ROW_H),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       fm.elidedText(pt.text if pt else it.name, Qt.TextElideMode.ElideLeft, int(NODE_W / 2 - 16)))
        if self._summary:
            p.setPen(QColor(theme.MUTED))
            p.drawText(QRectF(12, self._h - 22, NODE_W - 24, 18), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(self._summary, Qt.TextElideMode.ElideRight, NODE_W - 28))

    def _wired(self, port: str) -> bool:
        return any(e.edge.dst == self.node.id and e.edge.dst_port == port for e in self.edges)

    # -- interaction --------------------------------------------------------------------------
    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.node.x, self.node.y = self.pos().x(), self.pos().y()
            for e in self.edges:
                e.update_path()
        return super().itemChange(change, value)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.scene():
            self.scene().aboutToChange.emit()
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        self.scene().nodeActivated.emit(self.node.id)


class EdgeItem(QGraphicsPathItem):
    def __init__(self, edge: Edge, src: PortItem, dst: PortItem):
        super().__init__()
        self.edge, self.src, self.dst = edge, src, dst
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setZValue(0)
        self.setAcceptHoverEvents(True)
        self._hover = False
        self.update_path()

    def update_path(self):
        a, b = self.src.scenePos(), self.dst.scenePos()
        self.setPath(bezier(a, b))

    def shape(self) -> QPainterPath:
        s = QPainterPathStroker()
        s.setWidth(12)
        return s.createStroke(self.path())

    def paint(self, p: QPainter, opt, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = port_color(self.src.kind, self.src.dtype)
        if self.isSelected() or self._hover:
            c = QColor(theme.ACCENT)
        pen = QPen(c, 2.6 if self.src.kind == "exec" else 1.9)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(self.path())

    def hoverEnterEvent(self, e):
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self._hover = False
        self.update()


def bezier(a: QPointF, b: QPointF) -> QPainterPath:
    path = QPainterPath(a)
    dx = max(60.0, abs(b.x() - a.x()) * 0.5)
    path.cubicTo(QPointF(a.x() + dx, a.y()), QPointF(b.x() - dx, b.y()), b)
    return path


class GraphScene(QGraphicsScene):
    aboutToChange = pyqtSignal()          # emit *before* a structural edit (undo checkpoint)
    modified = pyqtSignal()               # graph changed in any way
    nodeSelected = pyqtSignal(object)     # node id or None
    nodeActivated = pyqtSignal(str)
    message = pyqtSignal(str)

    def __init__(self, graph: Graph):
        super().__init__()
        self.graph = graph
        self.node_items: dict[str, NodeItem] = {}
        self.edge_items: list[EdgeItem] = []
        self.group_items: dict[str, GroupItem] = {}
        self._drag_from: PortItem | None = None
        self._temp: QGraphicsPathItem | None = None
        self.setSceneRect(-4000, -3000, 8000, 6000)
        self.selectionChanged.connect(self._sel)

    # -- (re)building ------------------------------------------------------------------------
    def set_graph(self, graph: Graph):
        self.graph = graph
        self.rebuild()

    def rebuild(self):
        sel = [i for i, it in self.node_items.items() if it.isSelected()]
        self.blockSignals(True)
        self.clear()
        self.node_items, self.edge_items, self.group_items = {}, [], {}
        self.blockSignals(False)
        for n in self.graph.nodes.values():
            it = NodeItem(n)
            self.addItem(it)
            self.node_items[n.id] = it
        for e in self.graph.edges:
            self._add_edge_item(e)
        for g in getattr(self.graph, "groups", []) or []:
            self._spawn_group_item(g)
        for i in sel:
            if i in self.node_items:
                self.node_items[i].setSelected(True)

    def _add_edge_item(self, e: Edge):
        a, b = self.node_items.get(e.src), self.node_items.get(e.dst)
        if not a or not b:
            return
        pa, pb = a.find_port(e.src_port, True), b.find_port(e.dst_port, False)
        if not pa or not pb:
            return
        item = EdgeItem(e, pa, pb)
        self.addItem(item)
        a.edges.append(item)
        b.edges.append(item)
        self.edge_items.append(item)
        b.update()

    def refresh_node(self, node_id: str):
        """A node's props changed — re-lay it out and drop wires whose ports vanished."""
        it = self.node_items.get(node_id)
        if not it:
            return
        ins, outs = it.node.ports()
        expected = {(p.name, False) for p in ins} | {(p.name, True) for p in outs}
        current = {(p.name, p.output) for p in it.ports}
        before = len(self.graph.edges)
        self.graph.prune_edges()
        if expected != current or len(self.graph.edges) != before:
            sel = it.isSelected()
            self.rebuild()
            if sel and node_id in self.node_items:
                self.node_items[node_id].setSelected(True)
        else:
            it.refresh_summary()

    def _sel(self):
        items = [i for i in self.selectedItems() if isinstance(i, NodeItem)]
        self.nodeSelected.emit(items[0].node.id if len(items) == 1 else None)

    # -- editing -----------------------------------------------------------------------------------
    def add_node(self, type_id: str, pos: QPointF) -> Node:
        self.aboutToChange.emit()
        n = self.graph.add_node(type_id, pos.x(), pos.y())
        it = NodeItem(n)
        self.addItem(it)
        self.node_items[n.id] = it
        self.clearSelection()
        it.setSelected(True)
        self.modified.emit()
        return n

    def delete_selected(self):
        sel_nodes = [i.node.id for i in self.selectedItems() if isinstance(i, NodeItem)]
        sel_edges = [i.edge for i in self.selectedItems() if isinstance(i, EdgeItem)]
        if not sel_nodes and not sel_edges:
            return
        self.aboutToChange.emit()
        for e in sel_edges:
            if e in self.graph.edges:
                self.graph.edges.remove(e)
        for nid in sel_nodes:
            self.graph.remove_node(nid)
        self.rebuild()
        self.modified.emit()

    def duplicate_selected(self):
        sel = [i.node for i in self.selectedItems() if isinstance(i, NodeItem)]
        if not sel:
            return
        self.aboutToChange.emit()
        import copy
        ids = {}
        for n in sel:
            c = self.graph.add_node(n.type_id, n.x + 40, n.y + 40, copy.deepcopy(n.props))
            ids[n.id] = c.id
        for e in list(self.graph.edges):
            if e.src in ids and e.dst in ids:
                self.graph.edges.append(Edge(ids[e.src], e.src_port, ids[e.dst], e.dst_port))
        self.rebuild()
        self.clearSelection()
        for nid in ids.values():
            self.node_items[nid].setSelected(True)
        self.modified.emit()

    # -- connecting --------------------------------------------------------------------------------------

    def _spawn_group_item(self, g: dict):
        gid = g.get("id") or ("grp_" + __import__("uuid").uuid4().hex[:8])
        g["id"] = gid
        item = GroupItem(gid, g.get("title") or "Group", list(g.get("node_ids") or []), self)
        item.collapsed = bool(g.get("collapsed"))
        self.addItem(item)
        self.group_items[gid] = item
        item.relayout()
        return item

    def group_selected(self):
        ids = [i.node.id for i in self.selectedItems() if isinstance(i, NodeItem)]
        if len(ids) < 1:
            self.message.emit("Select one or more nodes to group")
            return
        self.aboutToChange.emit()
        import uuid
        gid = "grp_" + uuid.uuid4().hex[:8]
        g = {"id": gid, "title": "Group", "node_ids": ids, "collapsed": False}
        if not hasattr(self.graph, "groups") or self.graph.groups is None:
            self.graph.groups = []
        self.graph.groups.append(g)
        item = self._spawn_group_item(g)
        self.clearSelection()
        item.setSelected(True)
        self.modified.emit()
        self.message.emit(f"Grouped {len(ids)} node(s) — double-click to collapse")

    def ungroup_selected(self):
        groups = [i for i in self.selectedItems() if isinstance(i, GroupItem)]
        # also ungroup if nodes belong to a group
        if not groups:
            sel = {i.node.id for i in self.selectedItems() if isinstance(i, NodeItem)}
            for g in list(getattr(self.graph, "groups", []) or []):
                if sel & set(g.get("node_ids") or []):
                    gi = self.group_items.get(g["id"])
                    if gi:
                        groups.append(gi)
        if not groups:
            self.message.emit("Select a group to ungroup")
            return
        self.aboutToChange.emit()
        for gi in groups:
            # reveal nodes
            for nid in gi.node_ids:
                it = self.node_items.get(nid)
                if it:
                    it.setVisible(True)
            self.graph.groups = [g for g in self.graph.groups if g.get("id") != gi.group_id]
            self.removeItem(gi)
            self.group_items.pop(gi.group_id, None)
        self.modified.emit()
        self.message.emit("Ungrouped")

    def add_selection_to_group(self):
        ids = [i.node.id for i in self.selectedItems() if isinstance(i, NodeItem)]
        groups = list(getattr(self.graph, "groups", []) or [])
        if not ids or not groups:
            self.message.emit("Select nodes and ensure a group exists")
            return
        # add to first group for simplicity (or last)
        self.aboutToChange.emit()
        g = groups[-1]
        for nid in ids:
            if nid not in g["node_ids"]:
                g["node_ids"].append(nid)
        gi = self.group_items.get(g["id"])
        if gi:
            gi.node_ids = list(g["node_ids"])
            gi.relayout()
        self.modified.emit()
        self.message.emit(f"Added {len(ids)} node(s) to '{g.get('title', 'Group')}'")

    def rename_group(self, item: "GroupItem"):
        from PyQt6.QtWidgets import QInputDialog
        title, ok = QInputDialog.getText(None, "Rename group", "Name:", text=item.title)
        if ok and title.strip():
            self.aboutToChange.emit()
            item.title = title.strip()
            for g in getattr(self.graph, "groups", []) or []:
                if g.get("id") == item.group_id:
                    g["title"] = item.title
            item.update()
            self.modified.emit()

    def begin_connect(self, port: PortItem, pos: QPointF):
        # dragging from an occupied input detaches that wire and carries its source end instead
        if not port.output:
            existing = self.graph.incoming(port.node_item.node.id, port.name)
            if existing is not None and port.kind == "data":
                self.aboutToChange.emit()
                self.graph.edges.remove(existing)
                src_item = self.node_items[existing.src].find_port(existing.src_port, True)
                self.rebuild()
                port = self.node_items[existing.src].find_port(existing.src_port, True) or src_item
                self.modified.emit()
        self._drag_from = port
        self._temp = QGraphicsPathItem()
        pen = QPen(QColor(theme.ACCENT), 2, Qt.PenStyle.DashLine)
        self._temp.setPen(pen)
        self._temp.setZValue(5)
        self.addItem(self._temp)
        self._update_temp(pos)

    def _update_temp(self, pos: QPointF):
        a = self._drag_from.scenePos()
        self._temp.setPath(bezier(a, pos) if self._drag_from.output else bezier(pos, a))

    def mouseMoveEvent(self, e):
        if self._drag_from is not None:
            self._update_temp(e.scenePos())
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag_from is not None:
            start, self._drag_from = self._drag_from, None
            self.removeItem(self._temp)
            self._temp = None
            target = next((i for i in self.items(e.scenePos()) if isinstance(i, PortItem)), None)
            if target is None:
                target = self._nearest_port(e.scenePos(), start)
            if target is not None and target is not start:
                self._finish_connect(start, target)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def _nearest_port(self, pos: QPointF, start: PortItem) -> PortItem | None:
        """Dropping on a node's body connects to its first compatible port."""
        for it in self.items(pos):
            if isinstance(it, NodeItem) and it is not start.node_item:
                for p in it.ports:
                    if p.output != start.output and p.kind == start.kind:
                        return p
        return None

    def _finish_connect(self, a: PortItem, b: PortItem):
        out, inp = (a, b) if a.output else (b, a)
        if out.output == inp.output:
            self.message.emit("Connect an output port to an input port.")
            return
        ok, why = self.graph.can_connect(out.node_item.node.id, out.name, inp.node_item.node.id, inp.name)
        if not ok:
            self.message.emit(why)
            return
        self.aboutToChange.emit()
        self.graph.connect(out.node_item.node.id, out.name, inp.node_item.node.id, inp.name)
        self.rebuild()
        self.modified.emit()




class GroupItem(QGraphicsItem):
    """Collapsible visual group for organizing nodes on the canvas."""
    HEADER = 32
    PAD = 28  # margin around member nodes so the box never clips them

    def __init__(self, group_id: str, title: str, node_ids: list[str], scene: "GraphScene"):
        super().__init__()
        self.group_id = group_id
        self.title = title or "Group"
        self.node_ids = list(node_ids)
        self.collapsed = False
        self._scene_ref = scene
        self._press_local = None
        self._dragging = False
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setZValue(0)
        self._rect = QRectF(0, 0, 200, 80)
        self.relayout()

    def _member_items(self):
        return [self._scene_ref.node_items[i] for i in self.node_ids
                if i in self._scene_ref.node_items]

    def relayout(self):
        items = self._member_items()
        self.prepareGeometryChange()
        if not items:
            self._rect = QRectF(0, 0, 180, self.HEADER + 16)
            self.update()
            return
        # Use each node's full painted height, not a fixed 80px guess
        left = min(it.pos().x() for it in items)
        top = min(it.pos().y() for it in items)
        right = max(it.pos().x() + max(NODE_W, it.boundingRect().width()) for it in items)
        bottom = max(it.pos().y() + max(60.0, it.boundingRect().height()) for it in items)
        pad = self.PAD
        min_x = left - pad
        min_y = top - pad - self.HEADER
        max_x = right + pad
        max_y = bottom + pad
        self.setPos(min_x, min_y)
        w = max(180.0, max_x - min_x)
        h = self.HEADER + 12 if self.collapsed else max(self.HEADER + 24.0, max_y - min_y)
        self._rect = QRectF(0, 0, w, h)
        for it in items:
            it.setVisible(not self.collapsed)
            for ed in getattr(it, "edges", []):
                ed.setVisible(not self.collapsed)
        # Persist collapsed flag
        for g in getattr(self._scene_ref.graph, "groups", []) or []:
            if g.get("id") == self.group_id:
                g["collapsed"] = self.collapsed
                g["node_ids"] = list(self.node_ids)
        self.update()

    def boundingRect(self) -> QRectF:
        return self._rect.adjusted(-4, -4, 4, 4)

    def shape(self):
        from PyQt6.QtGui import QPainterPath
        path = QPainterPath()
        path.addRoundedRect(self._rect, 8, 8)
        return path

    def paint(self, p: QPainter, opt, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(theme.GRID_MAJOR)
        bg.setAlpha(35)
        border = QColor(theme.ACCENT if self.isSelected() else theme.GRID_MAJOR)
        p.setPen(QPen(border, 2, Qt.PenStyle.DashLine))
        p.setBrush(QBrush(bg))
        p.drawRoundedRect(self._rect, 8, 8)
        header = QRectF(0, 0, self._rect.width(), self.HEADER)
        hb = QColor(theme.ACCENT)
        hb.setAlpha(90)
        p.setBrush(QBrush(hb))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(header, 8, 8)
        # keep bottom of header square so it meets the body
        p.drawRect(QRectF(0, self.HEADER - 8, self._rect.width(), 8))
        p.setPen(QPen(QColor(theme.TEXT)))
        p.setFont(QFont("Sans", 10, QFont.Weight.Bold))
        mark = "▸  " if self.collapsed else "▾  "
        p.drawText(header.adjusted(12, 0, -12, 0),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   mark + self.title + f"  ({len(self.node_ids)})")
        # Hint on the right of the header
        p.setFont(QFont("Sans", 9))
        p.setPen(QPen(QColor(theme.MUTED if hasattr(theme, "MUTED") else theme.TEXT)))
        p.drawText(header.adjusted(12, 0, -12, 0),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   "click header to collapse" if not self.collapsed else "click to expand")

    def toggle_collapse(self):
        self.collapsed = not self.collapsed
        self.relayout()
        if self.scene():
            self.scene().modified.emit()
            self.scene().message.emit(
                "Group collapsed" if self.collapsed else "Group expanded")

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(e)
            return
        local = e.pos()
        self._press_local = local
        self._dragging = False
        self._press_pos = self.pos()
        self._member_origins = {
            nid: self._scene_ref.node_items[nid].pos()
            for nid in self.node_ids if nid in self._scene_ref.node_items
        }
        # Header click is reserved for collapse; body for drag/select
        if local.y() <= self.HEADER:
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press_local is not None and self._press_local.y() <= self.HEADER:
            # don't drag when interacting with header
            e.accept()
            return
        if self._press_local is not None:
            delta = e.pos() - self._press_local
            if abs(delta.x()) + abs(delta.y()) > 4:
                self._dragging = True
        super().mouseMoveEvent(e)
        if not getattr(self, "_member_origins", None):
            return
        delta = self.pos() - self._press_pos
        for nid, origin in self._member_origins.items():
            it = self._scene_ref.node_items.get(nid)
            if it:
                it.setPos(origin + delta)
                it.node.x, it.node.y = it.pos().x(), it.pos().y()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._press_local is not None:
            # Click on header (no meaningful drag) → toggle collapse
            if self._press_local.y() <= self.HEADER and not self._dragging:
                self.toggle_collapse()
                self._press_local = None
                e.accept()
                return
        self._press_local = None
        self._dragging = False
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        # Double-click anywhere on the group toggles as well
        self.toggle_collapse()
        e.accept()

    def itemChange(self, change, value):
        return super().itemChange(change, value)


class NodeView(QGraphicsView):
    def __init__(self, scene: GraphScene):
        super().__init__(scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        # Default: no rubber-band; empty left-drag pans. Shift+left rubber-bands.
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setAcceptDrops(True)
        self._panning = False
        self._last = QPointF()
        self.setBackgroundBrush(QColor(theme.CANVAS))

    def drawBackground(self, p: QPainter, rect: QRectF):
        super().drawBackground(p, rect)
        step = 24
        left, top = int(rect.left()) - int(rect.left()) % step, int(rect.top()) - int(rect.top()) % step
        minor, major = QPen(QColor(theme.GRID)), QPen(QColor(theme.GRID_MAJOR))
        x = left
        while x < rect.right():
            p.setPen(major if x % (step * 5) == 0 else minor)
            p.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))
            x += step
        y = top
        while y < rect.bottom():
            p.setPen(major if y % (step * 5) == 0 else minor)
            p.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))
            y += step

    # -- navigation -------------------------------------------------------------------------------------
    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        cur = self.transform().m11()
        if 0.25 < cur * f < 2.5:
            self.scale(f, f)

    def _is_empty_canvas(self, pos) -> bool:
        item = self.itemAt(pos)
        if item is None:
            return True
        # Port/node/edge/group are not empty
        while item is not None:
            if isinstance(item, (NodeItem, PortItem, EdgeItem, GroupItem)):
                return False
            item = item.parentItem()
        return True

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.MiddleButton:
            self._panning, self._last = True, e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            mods = e.modifiers()
            # Shift+left on empty (or anywhere) → rubber-band select
            if mods & Qt.KeyboardModifier.ShiftModifier:
                self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
                super().mousePressEvent(e)
                return
            # Left on empty space → pan
            if self._is_empty_canvas(e.pos()):
                self._panning, self._last = True, e.position()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.setDragMode(QGraphicsView.DragMode.NoDrag)
                e.accept()
                return
            # Left on a node: normal move/select (Ctrl adds to selection via Qt)
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._panning:
            d = e.position() - self._last
            self._last = e.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - d.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - d.y()))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._panning and e.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton):
            self._panning = False
            self.unsetCursor()
            e.accept()
            return
        super().mouseReleaseEvent(e)
        self.scene().modified.emit()

    def keyPressEvent(self, e):
        mods = e.modifiers()
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.scene().delete_selected()
        elif e.key() == Qt.Key.Key_D and mods & Qt.KeyboardModifier.ControlModifier:
            self.scene().duplicate_selected()
        elif e.key() == Qt.Key.Key_A and mods & Qt.KeyboardModifier.ControlModifier:
            for it in self.scene().node_items.values():
                it.setSelected(True)
        elif e.key() == Qt.Key.Key_G and mods & Qt.KeyboardModifier.ControlModifier:
            if mods & Qt.KeyboardModifier.ShiftModifier:
                self.scene().ungroup_selected()
            else:
                self.scene().group_selected()
        else:
            super().keyPressEvent(e)

    def fit_all(self):
        items = self.scene().node_items
        if not items:
            self.resetTransform()
            self.centerOn(0, 0)
            return
        rect = self.scene().itemsBoundingRect().adjusted(-60, -60, 60, 60)
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        if self.transform().m11() > 1.2:
            self.resetTransform()
            self.centerOn(rect.center())

    # -- add-node menu & drops ----------------------------------------------------------------------------
    def contextMenuEvent(self, e):
        pos = self.mapToScene(e.pos())
        item = self.itemAt(e.pos())
        menu = QMenu(self)
        # Walk up to GroupItem if clicking header area of children
        hit = item
        while hit is not None and not isinstance(hit, (NodeItem, EdgeItem, GroupItem)):
            hit = hit.parentItem()
        item = hit
        if isinstance(item, GroupItem):
            if not item.isSelected():
                self.scene().clearSelection()
                item.setSelected(True)
            menu.addAction("Collapse" if not item.collapsed else "Expand", item.toggle_collapse)
            menu.addAction("Ungroup", self.scene().ungroup_selected)
            menu.addAction("Rename group…", lambda: self.scene().rename_group(item))
            menu.addSeparator()
            menu.addAction("Delete group + nodes", self.scene().delete_selected)
            menu.exec(e.globalPos())
            return
        if isinstance(item, (NodeItem, EdgeItem)):
            if not item.isSelected():
                self.scene().clearSelection()
                item.setSelected(True)
            menu.addAction("Delete", self.scene().delete_selected)
            if isinstance(item, NodeItem):
                menu.addAction("Duplicate", self.scene().duplicate_selected)
                menu.addSeparator()
                menu.addAction("Group selected  (Ctrl+G)", self.scene().group_selected)
                menu.addAction("Ungroup  (Ctrl+Shift+G)", self.scene().ungroup_selected)
                menu.addAction("Add selection to group…", self.scene().add_selection_to_group)
            menu.exec(e.globalPos())
            return
        # empty canvas
        if any(isinstance(i, NodeItem) and i.isSelected() for i in self.scene().selectedItems()):
            menu.addAction("Group selected  (Ctrl+G)", self.scene().group_selected)
            menu.addSeparator()
        self.populate_add_menu(menu, lambda tid: self.scene().add_node(tid, pos))
        menu.exec(e.globalPos())

    @staticmethod
    def populate_add_menu(menu: QMenu, callback):
        cats: dict[str, QMenu] = {}
        for spec in all_specs():
            sub = cats.get(spec.category) or cats.setdefault(spec.category, menu.addMenu(spec.category))
            act = QAction(spec.title, sub)
            act.setToolTip(spec.description)
            act.triggered.connect(lambda _=False, t=spec.type_id: callback(t))
            sub.addAction(act)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()

    def dropEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            tid = bytes(e.mimeData().data(MIME)).decode()
            self.scene().add_node(tid, self.mapToScene(e.position().toPoint()))
            e.acceptProposedAction()
