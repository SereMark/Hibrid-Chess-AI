import h5py, torch
import chess, numpy as np
from itertools import product
from torch.utils.data import Dataset

class H5Dataset(Dataset):
    def __init__(s, p, i): s.p, s.i, s.f = p, i, None
    def __len__(s): return len(s.i)
    def __getitem__(s, x): 
        s.f = s.f or h5py.File(s.p, 'r')
        return tuple(torch.tensor(s.f[k][s.i[x]], dtype=t) for k, t in zip(['inputs', 'policy_targets', 'value_targets'], [torch.float32, torch.long, torch.float32]))
    def __del__(s): s.f and s.f.close()

class MoveMapping:
    def __init__(self):
        m = []
        for f, t in product(chess.SQUARES, repeat=2):
            if f != t:
                m.append(chess.Move(f, t))
                if chess.square_rank(t) in (0, 7):
                    for p in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
                        m.append(chess.Move(f, t, promotion=p))
        self.MOVE_MAPPING = dict(enumerate(m))
        self.INDEX_MAPPING = {v: k for k, v in self.MOVE_MAPPING.items()}
        self.TOTAL_MOVES = len(m)
    def get_move_by_index(self, i): return self.MOVE_MAPPING.get(i)
    def get_index_by_move(self, m): return self.INDEX_MAPPING.get(m)

move_mapping = MoveMapping()

def get_total_moves():
    return move_mapping.TOTAL_MOVES

def get_move_mapping():
    return move_mapping

def flip_board(b):
    return b.mirror()

def flip_move(m):
    return chess.Move(chess.square_mirror(m.from_square), chess.square_mirror(m.to_square), promotion=m.promotion)

def mirror_rank(b):
    f = b.fen().split()
    r = f[0].split("/")
    rf = "/".join(reversed(r))+" "+" ".join(f[1:])
    return chess.Board(rf)

def mirror_move_rank(m):
    fr = 7 - chess.square_rank(m.from_square)
    ff = chess.square_file(m.from_square)
    tr = 7 - chess.square_rank(m.to_square)
    tf = chess.square_file(m.to_square)
    return chess.Move(chess.square(tf, tr), chess.square(ff, fr), promotion=m.promotion)

def convert_single_board(b):
    x = np.zeros((64, 18), np.float32)
    piece_map = {
        (chess.PAWN, True):0,(chess.KNIGHT,True):1,(chess.BISHOP,True):2,(chess.ROOK,True):3,(chess.QUEEN,True):4,(chess.KING,True):5,
        (chess.PAWN,False):6,(chess.KNIGHT,False):7,(chess.BISHOP,False):8,(chess.ROOK,False):9,(chess.QUEEN,False):10,(chess.KING,False):11
    }
    for s, p in b.piece_map().items():
        i = piece_map.get((p.piece_type, p.color))
        if i is not None:
            x[s, i] = 1
    if b.turn:
        x[:,12] = 1
    ep = b.ep_square
    if ep is not None:
        x[ep,13] = 1
    wk = b.has_kingside_castling_rights(chess.WHITE)
    wq = b.has_queenside_castling_rights(chess.WHITE)
    bk = b.has_kingside_castling_rights(chess.BLACK)
    bq = b.has_queenside_castling_rights(chess.BLACK)
    if wk:
        x[:,14] = 1
    if wq:
        x[:,15] = 1
    if bk:
        x[:,16] = 1
    if bq:
        x[:,17] = 1
    return x

def convert_board_to_transformer_input(b):
    temp_board = b.copy()
    boards = [temp_board.copy()]
    history = list(temp_board.move_stack)
    for _ in range(7):
        if not history: break
        history.pop()
        temp_board.pop()
        boards.insert(0, temp_board.copy())
    while len(boards) < 8:
        boards.insert(0, boards[0].copy())
    return np.concatenate([convert_single_board(x) for x in boards], axis=1)