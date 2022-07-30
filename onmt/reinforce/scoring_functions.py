#!/usr/bin/env python
from __future__ import print_function, division
import os
import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit.Chem.QED import qed
from rdkit.Chem import AllChem, rdMolAlign
from rdkit.Chem import Descriptors
from rdkit import DataStructs
from sklearn import svm
import joblib
from sklearn import ensemble
import time
import pickle
import re
import sys
import threading
import pexpect
import importlib
import multiprocessing
from onmt.reinforce.score_util import calc_SC_RDKit_score
from onmt.reinforce import sascorer

from rdkit import rdBase, RDLogger
RDLogger.DisableLog('rdApp.*')  # https://github.com/rdkit/rdkit/issues/2683
rdBase.DisableLog('rdApp.error')

"""Scoring function should be a class where some tasks that are shared for every call
   can be reallocated to the __init__, and has a __call__ method which takes a single SMILES of
   argument and returns a float. A multiprocessing class will then spawn workers and divide the
   list of SMILES given between them.

   Passing *args and **kwargs through a subprocess call is slightly tricky because we need to know
   their types - everything will be a string once we have passed it. Therefor, we instead use class
   attributes which we can modify in place before any subprocess is created. Any **kwarg left over in
   the call to get_scoring_function will be checked against a list of (allowed) kwargs for the class
   and if a match is found the value of the item will be the new value for the class.

   If num_processes == 0, the scoring function will be run in the main process. Depending on how
   demanding the scoring function is and how well the OS handles the multiprocessing, this might
   be faster than multiprocessing in some cases."""

class NOS():
    """Scores structures based on not containing sulphur."""

    kwargs = ["src", "ref"]
    src = ""
    ref = ""

    def __init__(self):
        self.src_new = remove_dummys(self.src)

    def __call__(self, smile):
        mol = Chem.MolFromSmiles(smile)
        if mol:
            isstandard = juice_is_standard_contains_fregments(smile, self.src_new)

            if isstandard:

                has_sulphur = [16 not in [atom.GetAtomicNum() for atom in mol.GetAtoms()]]
                # print(f'src:{src,smile,has_sulphur.count(True)}')
                return float(has_sulphur.count(True))
            else:
                return 0.0
        return 0.0


class MW():
    """Scores structures based on MW."""

    kwargs = ["src", "ref"]
    src = ""
    ref = ""
    goal_molecular_weight = 600

    def __init__(self):
        self.src_new = remove_dummys(self.src)
        # pass

    def __call__(self, smile):
        # gtruth_structure is the godden structure, smile is generated by agent
        mol = Chem.MolFromSmiles(smile)
        if mol:
            isstandard = juice_is_standard_contains_fregments(smile, self.src_new)

            if isstandard:
                weight = Descriptors.MolWt(mol)
                Rmw = 1 - 10e-6 * ((weight - self.goal_molecular_weight) ** 2)
                return Rmw
            else:
                return -10.0
        return -10.0


class QED():
    """Scores structures based on QED"""

    kwargs = ["src", "ref"]
    src = ""
    ref = ""
    def __init__(self):
        self.src_new = remove_dummys(self.src)
        # pass

    def __call__(self, smile):
        # gtruth_structure is the godden structure, smile is generated by agent
        mol = Chem.MolFromSmiles(smile)
        if mol:
            isstandard = juice_is_standard_contains_fregments(smile, self.src_new)
            if isstandard:
                score = qed(mol)
                return score
            else:
                return 0.0

        return 0.0


class SIM_3D():
    """Scores structures based on not containing sulphur."""

    kwargs = ["src", "ref"]
    src = ""
    ref = ""
    def __init__(self):
        # print(f'{self.src,self.ref}')
        self.src_new = remove_dummys(self.src)
        ref_mol = Chem.MolFromSmiles(self.ref)
        self.ref_mol = Chem.AddHs(ref_mol)
        Chem.AllChem.EmbedMolecule(self.ref_mol, randomSeed=10)
        Chem.AllChem.UFFOptimizeMolecule(self.ref_mol)


    def __call__(self, smile):
        # gtruth_structure is the godden structure, smile is generated by agent
        gen_mol = Chem.MolFromSmiles(smile)
        if gen_mol is None:
            return 0

        # isstandard = juice_is_standard_contains_fregments2(smile, self.src_new)
        isstandard = juice_is_standard_contains_fregments(smile, self.src_new)
        # if yes calculate score
        if isstandard:
            try:
                gen_mol = Chem.AddHs(gen_mol)
                # print(f'add H ref:{Chem.MolToSmiles(gen_mol)}')
                # print(f'add H gen:{Chem.MolToSmiles(Chem.AddHs(Chem.MolFromSmiles(self.ref)))}')
                Chem.AllChem.EmbedMolecule(gen_mol, randomSeed=10)
                Chem.AllChem.UFFOptimizeMolecule(gen_mol)
                # print(f'UFFOptimizeMolecule ref:{Chem.MolToSmiles(self.ref_mol)}')
                # print(f'UFFOptimizeMolecule gen:{Chem.MolToSmiles(gen_mol)}')
                pyO3A = rdMolAlign.GetO3A(gen_mol, self.ref_mol).Align()
                # print(self.src)
                # print(self.ref)
                # print(smile)
                # print(calc_SC_RDKit_score(gen_mol, self.ref_mol))
                return calc_SC_RDKit_score(gen_mol, self.ref_mol)
            except Exception:
                return 0.0
        else:
            return 0



# 对比学习的loss max/(min+0.1)
class SIM_3D1():
    """Scores structures based on not containing sulphur."""

    kwargs = ["src", "ref"]
    src = ""
    ref = ""
    def __init__(self):
        # print(f'{self.src,self.ref}')
        self.src_new = remove_dummys(self.src)
        ref_mol = Chem.MolFromSmiles(self.ref)
        self.query_fp = AllChem.GetMorganFingerprint(ref_mol, 2, useCounts=True, useFeatures=True)
        self.ref_mol = Chem.AddHs(ref_mol)
        Chem.AllChem.EmbedMolecule(self.ref_mol, randomSeed=10)
        Chem.AllChem.UFFOptimizeMolecule(self.ref_mol)


    def __call__(self, smile):
        # gtruth_structure is the godden structure, smile is generated by agent
        gen_mol = Chem.MolFromSmiles(smile)
        if gen_mol is None:
            return 0

        # isstandard = juice_is_standard_contains_fregments2(smile, self.src_new)
        isstandard = juice_is_standard_contains_fregments(smile, self.src_new)
        # if yes calculate score
        if isstandard:
            try:
                fp = AllChem.GetMorganFingerprint(gen_mol, 2, useCounts=True, useFeatures=True)
                score_tanimoto = DataStructs.TanimotoSimilarity(self.query_fp, fp)
                gen_mol = Chem.AddHs(gen_mol)
                Chem.AllChem.EmbedMolecule(gen_mol, randomSeed=10)
                Chem.AllChem.UFFOptimizeMolecule(gen_mol)
                pyO3A = rdMolAlign.GetO3A(gen_mol, self.ref_mol).Align()
                
                # return calc_SC_RDKit_score(gen_mol, self.ref_mol)
                return calc_SC_RDKit_score(gen_mol, self.ref_mol)/(0.1+score_tanimoto)
            except Exception:
                return 0.0
        else:
            return 0

# 直接控制2d_sim<w，不然就给0分
class SIM_3D2():
    """Scores structures based on not containing sulphur."""

    kwargs = ["src", "ref", "w"]
    src = ""
    ref = ""
    w = 2.5
    def __init__(self):
        # print(f'{self.src,self.ref}')
        self.src_new = remove_dummys(self.src)
        ref_mol = Chem.MolFromSmiles(self.ref)
        self.target_tanimoto_score = self.w
        self.query_fp = AllChem.GetMorganFingerprint(ref_mol, 2, useCounts=True, useFeatures=True)
        self.ref_mol = Chem.AddHs(ref_mol)
        Chem.AllChem.EmbedMolecule(self.ref_mol, randomSeed=10)
        Chem.AllChem.UFFOptimizeMolecule(self.ref_mol)


    def __call__(self, smile):
        # gtruth_structure is the godden structure, smile is generated by agent
        gen_mol = Chem.MolFromSmiles(smile)
        if gen_mol is None:
            return 0

        # isstandard = juice_is_standard_contains_fregments2(smile, self.src_new)
        isstandard = juice_is_standard_contains_fregments(smile, self.src_new)
        # if yes calculate score
        if isstandard:
            try:
                fp = AllChem.GetMorganFingerprint(gen_mol, 2, useCounts=True, useFeatures=True)
                score_tanimoto = DataStructs.TanimotoSimilarity(self.query_fp, fp)
            
                if (score_tanimoto>self.target_tanimoto_score):
                    return 0.0
                else:
                    gen_mol = Chem.AddHs(gen_mol)
                    Chem.AllChem.EmbedMolecule(gen_mol, randomSeed=10)
                    Chem.AllChem.UFFOptimizeMolecule(gen_mol)
                    pyO3A = rdMolAlign.GetO3A(gen_mol, self.ref_mol).Align()
                    return calc_SC_RDKit_score(gen_mol, self.ref_mol)
                
            except Exception:
                return 0.0
        else:
            return 0


class tanimoto():
    """Scores structures based on Tanimoto similarity to a query structure.
       Scores are only scaled up to k=(0,1), after which no more reward is given."""

    kwargs = ["k", "src", "ref"]
    k = 0.8
    query_structure = "Cc1ccc(cc1)c2cc(nn2c3ccc(cc3)S(=O)(=O)N)C(F)(F)F"
    src = ""
    ref = ""
    def __init__(self):
        query_mol = Chem.MolFromSmiles(self.ref)
        self.query_fp = AllChem.GetMorganFingerprint(query_mol, 2, useCounts=True, useFeatures=True)
        self.src_new = remove_dummys(self.src)

    def __call__(self, smile):
        mol = Chem.MolFromSmiles(smile)
        if mol is None:
            return 0.0
        # isstandard = juice_is_standard_contains_fregments2(smile, self.src_new)
        isstandard = juice_is_standard_contains_fregments(smile, self.src_new)
        if isstandard:
            fp = AllChem.GetMorganFingerprint(mol, 2, useCounts=True, useFeatures=True)
            score = DataStructs.TanimotoSimilarity(self.query_fp, fp)
            score = min(score, self.k) / self.k
            return float(score)
        return 0.0

# Multi-objective optimization
class M_SIM_QED():
    """Scores structures based on Tanimoto similarity to a query structure.
       Scores are only scaled up to k=(0,1), after which no more reward is given.
       score = w * SIM(s) + (1-w) * QED(s)                                      """

    kwargs = ["src", "ref", "w"]
    src = ""
    ref = ""
    w = 0.0
    def __init__(self):
        query_mol = Chem.MolFromSmiles(self.ref)
        self.query_fp = AllChem.GetMorganFingerprint(query_mol, 2, useCounts=True, useFeatures=True)
        self.src_new = remove_dummys(self.src)
        # print(f'w:{self.w}')
    def __call__(self, smile):
        # gtruth_structure is the godden structure, smile is generated by agent
        mol = Chem.MolFromSmiles(smile)
        if mol is None:
            return 0.0

        isstandard = juice_is_standard_contains_fregments(smile, self.src_new)
        if isstandard:
            qed_score = qed(mol)
            fp = AllChem.GetMorganFingerprint(mol, 2, useCounts=True, useFeatures=True)
            tanimoto_score = DataStructs.TanimotoSimilarity(self.query_fp, fp)
            score = self.w*tanimoto_score + (1-self.w)*qed_score
            return float(score)
        return 0.0

class activity_model():
    """Scores based on an jak3 active RandomForestRegressor for activity."""

    kwargs = ["src", "ref", "clf_path"]
    src = ""
    ref = ""
    clf_path = 'data/clf_jak3_active.pkl'

    def __init__(self):
        self.src_new = remove_dummys(self.src)
        with open(self.clf_path, "rb") as f:
            self.clf = joblib.load(f)

    def __call__(self, smile):
        mol = Chem.MolFromSmiles(smile)
        if mol is None:
            return 0.0

        isstandard = juice_is_standard_contains_fregments(smile, self.src_new)
        if isstandard:
            fp = activity_model.fingerprints_from_mol(mol)
            score = self.clf.predict(fp)
            return float(score)
        return 0.0

    @classmethod
    def fingerprints_from_mol(cls, mol):
        fp = AllChem.GetMorganFingerprint(mol, 3, useCounts=True, useFeatures=True)
        size = 2048
        nfp = np.zeros((1, size), np.int32)
        for idx,v in fp.GetNonzeroElements().items():
            nidx = idx%size
            nfp[0, nidx] += int(v)
        return nfp

class CLOGP():
    """Scores structures based on ClogP."""
    kwargs = ["src", "ref", "w"]
    src = ""
    ref = ""
    w = 2.5

    def __init__(self):
        self.src_new = remove_dummys(self.src)
        self.goal_ClogP = self.w

    def __call__(self, smile):
        # gtruth_structure is the godden structure, smile is generated by agent
        mol = Chem.MolFromSmiles(smile)
        if mol:
            isstandard = juice_is_standard_contains_fregments(smile, self.src_new)
            if isstandard:
                mol_ClogP = Chem.Crippen.MolLogP(mol)
                # Rclogp = max(0.0, 1 - (1/10) * ((mol_ClogP-self.goal_ClogP)**2))
                Rclogp = max(0.0, 1 - (1/6)*abs(mol_ClogP - self.goal_ClogP))
                return Rclogp
            else:
                return 0.0
        return 0.0

class linker_length():
    """Scores structures based on linker_length"""

    kwargs = ["src", "ref", "w"]
    src = ""
    ref = ""
    w = 0
    def __init__(self):
        self.src_new = remove_dummys(self.src)
        self.goal_length = self.w


    def __call__(self, smile):
        # gtruth_structure is the godden structure, smile is generated by agent
        mol = Chem.MolFromSmiles(smile)
        if mol:
            isstandard = juice_is_standard_contains_fregments(smile, self.src_new)
            if isstandard:
                linker = get_linker(smile, self.src_new)
                if linker:
                    linker_mol = Chem.MolFromSmiles(linker)
                    if linker_mol:
                        linker_site_idxs = [atom.GetIdx() for atom in linker_mol.GetAtoms() if atom.GetAtomicNum() == 0]
                        linker_length = len(Chem.rdmolops.GetShortestPath(linker_mol, \
                                                                        linker_site_idxs[0], linker_site_idxs[1])) - 2
                        return max(0.0, 1 - 1/5 * abs(linker_length-self.goal_length))
                    else:
                        return 0.0
                else:
                    return 0.0
            else:
                return 0.0
        return 0.0


class QED_SA():
    """Scores structures based on QED"""

    kwargs = ["src", "ref"]
    src = ""
    ref = ""
    def __init__(self):
        self.src_new = remove_dummys(self.src)

    def __call__(self, smile):
        # gtruth_structure is the godden structure, smile is generated by agent
        mol = Chem.MolFromSmiles(smile)
        if mol:
            isstandard = juice_is_standard_contains_fregments(smile, self.src_new)
            if isstandard:
                qed_score = qed(mol)
                sa_score = sascorer.calculateScore(mol)
                return max(0, qed_score - (0.1*sa_score))
            else:
                return 0.0

        return 0.0

def get_linker(gen, frags):
    m = Chem.MolFromSmiles(gen)
    matchs = m.GetSubstructMatches(Chem.MolFromSmiles(frags))

    for match in matchs:
        # remove fragments
        atoms = m.GetNumAtoms()
        atoms_list = list(range(atoms))
        for i in match:
            atoms_list.remove(i)

        linker_list = atoms_list.copy()

        # add sites
        for i in atoms_list:
            atom = m.GetAtomWithIdx(i)
            for j in atom.GetNeighbors():
                linker_list.append(j.GetIdx())

        linker_list = list(set(linker_list))
        sites = list(set(linker_list).difference(set(atoms_list)))

        # get linking bonds
        bonds = []
        for i in sites:
            atom = m.GetAtomWithIdx(i)
            for j in atom.GetNeighbors():
                if j.GetIdx() in atoms_list:
                    b = m.GetBondBetweenAtoms(i, j.GetIdx())
                    bonds.append(b.GetIdx())
        bonds = list(set(bonds))

        if not bonds:
            return ""

        # get the linker which has two "*"
        bricks = Chem.FragmentOnBonds(m, bonds)  # dummyLabels=labels
        smi = Chem.MolToSmiles(bricks)
        pattern = re.compile(r"\[\d+\*?\]")
        for s in smi.split("."):
            count = pattern.findall(s)
            if len(count) == 2:
                s = s.replace(count[0], "[*]")
                linker_smi = s.replace(count[1], "[*]")
                try:
                    linker_smi = Chem.MolToSmiles(Chem.MolFromSmiles(linker_smi))
                except:
                    pass
                    # print(gen, frags, linker_smi)
                return linker_smi

def juice_is_standard_contains_fregments(gen, frags):
    "input generated molecules and the starting fragments of original molecules \
     return to the generated linker and  the two linker sites in fragments"
    try:
        m = Chem.MolFromSmiles(gen)
        matches = m.GetSubstructMatches(Chem.MolFromSmiles(frags))
    except:
        return false
    else:
        if matches:

            atoms = m.GetNumAtoms()
            for index,match in enumerate(matches):
                atoms_list = list(range(atoms))
                for i in match:
                    atoms_list.remove(i)

                linker_list = atoms_list.copy()
                for i in atoms_list:
                    atom = m.GetAtomWithIdx(i)
                    for j in atom.GetNeighbors():
                        linker_list.append(j.GetIdx())
                linker_list = list(set(linker_list))
                sites = list(set(linker_list).difference(set(atoms_list)))

                if len(sites) == 2:
                    return True
        else:
            return False
    return False


def juice_is_standard_contains_fregments2(gen, frags):
    "input generated molecules and the starting fragments of original molecules \
     return to the generated linker and  the two linker sites in fragments"

    m = Chem.MolFromSmiles(gen)
    matches = m.GetSubstructMatches(Chem.MolFromSmiles(frags))
    if matches:
        return True
    return False


def remove_dummys(smi_string):
    try:
        smi = Chem.MolToSmiles(Chem.RemoveHs(AllChem.ReplaceSubstructs(Chem.MolFromSmiles(smi_string), \
                                                                       Chem.MolFromSmiles('*'), \
                                                                       Chem.MolFromSmiles('[H]'), True)[0]))
    except:
        smi = ""

    return smi


class Worker():
    """A worker class for the Multiprocessing functionality. Spawns a subprocess
       that is listening for input SMILES and inserts the score into the given
       index in the given list."""
    def __init__(self, scoring_function=None, **kwargs):
        """The score_re is a regular expression that extracts the score from the
           stdout of the subprocess. This means only scoring functions with range
           0.0-1.0 will work, for other ranges this re has to be modified."""

        # func_class = getattr(importlib.import_module("scoring_functions"), scoring_function)
        func_class = getattr(sys.modules[__name__], scoring_function)
        # paras = sys.argv[2:] if len(sys.argv) > 2 else None
        # print('func_class: ',func_class)
        # if kwargs:
        for key, value in kwargs.items():
            # print(f'{key}:{value}')
            if key in func_class.kwargs:
                setattr(func_class, key, value)
        self.proc = func_class()


    def __call__(self, smile):
        # print(f'pro sendline smile: {smile}')
        return self.proc(smile)

class Multiprocessing():
    """Class for handling multiprocessing of scoring functions. OEtoolkits cant be used with
       native multiprocessing (cant be pickled), so instead we spawn threads that create
       subprocesses."""
    def __init__(self, num_processes=None, scoring_function=None, **kwargs):
        # print('调用multiprocessing的init方法')
        self.n = num_processes
        # self.pool = multiprocessing.Pool(processes=num_processes)
        self.worker = Worker(scoring_function=scoring_function, **kwargs)
        # self.workers = [Worker(scoring_function=scoring_function, **kwargs) for _ in range(num_processes)]

    # def alive_workers(self):
    #     return [i for i, worker in enumerate(self.workers) if worker.is_alive()]

    def __call__(self, smiles):
        pool = multiprocessing.Pool(processes=self.n)
        scores = pool.map(self.worker, smiles)
        pool.close()
        pool.join()
        return np.array(scores, dtype=np.float32)


class Singleprocessing():
    """Adds an option to not spawn new processes for the scoring functions, but rather
       run them in the main process."""
    def __init__(self, scoring_function=None):
        self.scoring_function = scoring_function()

    def __call__(self, smiles):
        scores = [self.scoring_function(smile) for smile in smiles]
        return np.array(scores, dtype=np.float32)

def get_scoring_function(scoring_function, num_processes=None, **kwargs):
    """Function that initializes and returns a scoring function by name"""
    scoring_function_classes = [tanimoto, activity_model, NOS, CLOGP, linker_length, QED_SA, MW, QED, SIM_3D, SIM_3D1, SIM_3D2, M_SIM_QED]
    scoring_functions = [f.__name__ for f in scoring_function_classes]
    scoring_function_class = [f for f in scoring_function_classes if f.__name__ == scoring_function][0]

    if scoring_function not in scoring_functions:
        raise ValueError("Scoring function must be one of {}".format([f for f in scoring_functions]))

    for k, v in kwargs.items():
        if k in scoring_function_class.kwargs:
            setattr(scoring_function_class, k, v)
    # print(f'train_agent.scoring_function:::  scoring_function_class{scoring_function_class}')
    # <class 'scoring_functions.activity_model'>
    if num_processes == 0:
        return Singleprocessing(scoring_function=scoring_function_class)

    # print(f'get_scoring_function: {scoring_function}')      # str   similarity_3d
    # print(f'get_scoring_function: {scoring_function_class}')        # <class 'scoring_functions.similarity_3d'>
    return Multiprocessing(scoring_function=scoring_function, num_processes=num_processes, **kwargs)


