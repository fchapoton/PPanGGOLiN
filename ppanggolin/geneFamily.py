#!/usr/bin/env python3
# coding: utf8

# default libraries
from __future__ import annotations
from collections import defaultdict
import logging

# installed libraries
from typing import Dict, Generator, Set
import gmpy2

# local libraries
from ppanggolin.edge import Edge
from ppanggolin.genome import Gene, Organism
from ppanggolin.metadata import MetaFeatures


class GeneFamily(MetaFeatures):
    """
    This represents a single gene family. It will be a node in the pangenome graph, and be aware of its genes and edges.

    Methods:
        - named_partition: returns a meaningful name for the partition associated with the family.
        - neighbors: returns all the GeneFamilies that are linked with an edge.
        - edges: returns all Edges that are linked to this gene family.
        - genes: returns all the genes associated with the family.
        - organisms: returns all the Organisms that have this gene family.
        - spots: returns all the spots associated with the family.
        - modules: returns all the modules associated with the family.
        - number_of_neighbor: returns the number of neighbor GeneFamilies.
        - number_of_edges: returns the number of edges.
        - number_of_genes: returns the number of genes.
        - number_of_organisms: returns the number of organisms.
        - number_of_spots: returns the number of spots.
        - number_of_modules: returns the number of modules.
        - set_edge: sets an edge between the current family and a target family.
        - add_sequence: assigns a protein sequence to the gene family.
        - add_gene: adds a gene to the gene family and sets the gene's family accordingly.
        - add_spot: adds a spot to the gene family.
        - add_module: adds a module to the gene family.
        - Mk_bitarray: produces a bitarray representing the presence/absence of the family in the pangenome using the provided index.
        - get_org_dict: returns a dictionary of organisms as keys and sets of genes as values.
        - get_genes_per_org: returns the genes belonging to the gene family in the given organism.

    Fields:
        - name: the name of the gene family.
        - ID: the internal identifier of the gene family.
        - removed: a boolean indicating whether the family has been removed from the main graph.
        - sequence: the protein sequence associated with the family.
        - Partition: the partition associated with the family.
    """

    def __init__(self, family_id: int, name: str):
        # TODO edges as genes in contig to get and set
        """Constructor method
        :param family_id: The internal identifier to give to the gene family
        :type family_id: any
        :param name: The name of the gene family (to be printed in output files)
        :type name: str
        """
        assert isinstance(family_id, int), "GeneFamily object id should be an integer"
        assert isinstance(name, str), "GeneFamily object name should be a string"
        assert name != '', "GeneFamily object cannot be created with an empty name"

        super().__init__()
        self.name = str(name)
        self.ID = family_id
        self._edges = {}
        self._genePerOrg = defaultdict(set)
        self._genes_getter = {}
        self.removed = False  # for the repeated family not added in the main graph
        self.sequence = ""
        self.partition = ""
        self._spots = set()
        self._modules = set()
        self.bitarray = None

    def __repr__(self) -> str:
        """Family representation
        """
        return f"{self.ID}: {self.name}"



    def __len__(self) -> int:
        return len(self._genes_getter)

    def __setitem__(self, identifier: str, gene: Gene):
        """ Set gene to Gene Family

        :param identifier: ID of the gene
        :param gene: Gene object to add

        :raises TypeError: If the gene is not instance Gene
        :raises TypeError: If the identifier is not instance string
        :raises ValueError: If a gene in getter already exists at the name
        """
        # TODO look at change start for position

        if not isinstance(gene, Gene):
            raise TypeError(f"'Gene' type was expected but you provided a '{type(gene)}' type object")
        if not isinstance(identifier, str):
            raise TypeError(f"Gene ID should be a string. You provided a '{type(identifier)}' type object")
        if identifier in self._genes_getter:
            raise KeyError(f"Gene with name {identifier} already exists in the gene family")
        self._genes_getter[identifier] = gene

    # TODO define eq function

    # retrieve gene by start position
    def __getitem__(self, identifier: str) -> Gene:
        """Get the gene for the given name

        :param identifier: ID of the gene in the gene family

        :return:  Wanted gene

        :raises TypeError: If the identifier is not instance string
        :raises KeyError: Gene with the given identifier does not exist in the contig
        """
        if not isinstance(identifier, str):
            raise TypeError(f"Gene ID should be a string. You provided a '{type(identifier)}' type object")
        try:
            return self._genes_getter[identifier]
        except KeyError:
            raise KeyError(f"Gene with the ID: {identifier} does not exist in the family")

    def __delitem__(self, identifier: str):
        """Remove the gene for the given name in the gene family

        :param position: ID of the gene in the family

        :raises TypeError: If the identifier is not instance string
        :raises KeyError: Gene with the given identifier does not exist in the contig
        """
        if not isinstance(identifier, str):
            raise TypeError(f"Gene ID should be a string. You provided a '{type(identifier)}' type object")
        try:
            del self._genes_getter[identifier]
        except KeyError:
            raise KeyError(f"Gene with the name: {identifier} does not exist in the family")

    def add(self, gene: Gene):
        """Add a gene to the gene family, and sets the gene's :attr:family accordingly.

        :param gene: The gene to add

        :raises TypeError: If the provided `gene` is of the wrong type
        """
        if not isinstance(gene, Gene):
            raise TypeError(f"'Gene' type object was expected, but '{type(gene)}' type object was provided.")
        self[gene.ID] = gene
        gene.family = self
        if gene.organism is not None:
            self._genePerOrg[gene.organism].add(gene)

    def get(self, identifier: str) -> Gene:
        """Get a gene by its name

        :param identifier: ID of the gene

        :return: Wanted gene

        :raises TypeError: If the identifier is not instance string
        """
        if not isinstance(identifier, str):
            raise TypeError(f"Gene ID should be a string. You provided a '{type(identifier)}' type object")
        return self[identifier]

    def remove(self, identifier):
        """Remove a gene by its name

        :param identifier: Name of the gene

        :return: Wanted gene

        :raises TypeError: If the identifier is not instance string
        """
        if not isinstance(identifier, str):
            raise TypeError(f"Gene ID should be a string. You provided a '{type(identifier)}' type object")
        del self[identifier]

    #TODO define __eq__

    @property
    def named_partition(self) -> str:
        """Reads the partition attribute and returns a meaningful name

        :return: The partition name of the gene family

        :raises ValueError: If the gene family has no partition assigned
        """
        if self.partition == "":
            raise ValueError("The gene family has not beed associated to a partition")
        if self.partition.startswith("P"):
            return "persistent"
        elif self.partition.startswith("C"):
            return "cloud"
        elif self.partition.startswith("S"):
            return "shell"
        else:
            return "undefined"

    @property
    def neighbors(self) -> Generator[GeneFamily, None, None]:
        """Returns all the GeneFamilies that are linked with an edge

        :return: Neighbors
        """
        for neighbor in self._edges.keys():
            yield neighbor

    @property
    def edges(self) -> Generator[Edge, None, None]:
        """Returns all Edges that are linked to this gene family

        :return: Edges of the gene family
        """
        for edge in self._edges.values():
            yield edge

    @property
    def genes(self):
        """Return all the genes belonging to the family

        :return: Generator of genes
        """
        for gene in self._genes_getter.values():
            yield gene

    @property
    def organisms(self) -> Generator[Organism, None, None]:
        """Returns all the Organisms that have this gene family

        :return: Organisms that have this gene family
        """
        if len(self._genePerOrg) == 0:
            _ = self.get_org_dict()
        for org in self._genePerOrg.keys():
            yield org

    @property
    def spots(self) -> Generator[Spot, None, None]:
        """Return all the spots belonging to the family

        :return: Generator of spots
        """
        for spot in self._spots:
            yield spot

    @property
    def modules(self) -> Generator[Module, None, None]:
        """Return all the modules belonging to the family

        :return: Generator of modules
        """
        for module in self._modules:
            yield module
    @property
    def number_of_neighbors(self) -> int:
        """Get the number of neighbor for the current gene family
        """
        return len(self._edges.keys())

    @property
    def number_of_edges(self) -> int:
        """Get the number of edges for the current gene family
        """
        return len(self._edges.values())

    @property
    def number_of_genes(self) -> int:
        """Get the number of genes for the current gene family
        """
        return len(self._genes)

    @property
    def number_of_organisms(self) -> int:
        """Get the number of organisms for the current gene family
        """
        if len(self._genePerOrg) == 0:
            _ = self.get_org_dict()
        return len(self._genePerOrg.keys())

    @property
    def number_of_spots(self) -> int:
        """Get the number of spots for the current gene family
        """
        return len(self._spots)

    @property
    def number_of_modules(self) -> int:
        """Get the number of modules for the current gene family
        """
        return len(self._modules)

    def set_edge(self, target: GeneFamily, edge: Edge):
        """Set the edge between the gene family and another one

        :param target: Neighbor family
        :param edge: Edge connecting families
        """
        self._edges[target] = edge

    def add_sequence(self, seq: str):
        """Assigns a protein sequence to the gene family.

        :param seq: The sequence to add to the gene family
        """
        assert isinstance(seq, str), "Sequence must be a string"

        self.sequence = seq

    def add_spot(self, spot: Spot):
        """Add the given spot to the family

        :param spot: Spot belonging to the family
        """
        from ppanggolin.region import Spot   # prevent circular import error
        if not isinstance(spot, Spot):
            raise TypeError(f"A spot object is expected, you give a {type(spot)}")
        self._spots.add(spot)

    def add_module(self, module: Module):
        """Add the given module to the family

        :param module: Module belonging to the family
        """
        from ppanggolin.region import Module   # prevent circular import error
        if not isinstance(module, Module):
            raise TypeError(f"A module object is expected, you give a {type(module)}")
        self._modules.add(module)

    def mk_bitarray(self, index: Dict[Organism, int], partition: str = 'all'):
        """Produces a bitarray representing the presence/absence of the family in the pangenome using the provided index
        The bitarray is stored in the :attr:`bitarray` attribute and is a :class:`gmpy2.xmpz` type.

        :param index: The index computed by :func:`ppanggolin.pangenome.Pangenome.getIndex`
        :param partition: partition used to compute bitarray
        """
        self.bitarray = gmpy2.xmpz()  # pylint: disable=no-member
        if partition == 'all':
            logging.getLogger("PPanGGOLiN").debug(f"all")
            for org in self.organisms:
                self.bitarray[index[org]] = 1
        elif partition in ['shell', 'cloud']:
            logging.getLogger("PPanGGOLiN").debug(f"shell, cloud")
            if self.named_partition == partition:
                for org in self.organisms:
                    self.bitarray[index[org]] = 1
        elif partition == 'accessory':
            logging.getLogger("PPanGGOLiN").debug(f"accessory")
            if self.named_partition in ['shell', 'cloud']:
                for org in self.organisms:
                    self.bitarray[index[org]] = 1

    def get_org_dict(self) -> Dict[Organism, Set[Gene]]:
        """Returns the organisms and the genes belonging to the gene family

        :return: A dictionnary of organism as key and set of genes as values
        """
        if len(self._genePerOrg) == 0:
            for gene in self.genes:
                if gene.organism is None:
                    raise AttributeError(f"Gene: {gene.name} is not fill with organism")
                self._genePerOrg[gene.organism].add(gene)
        return self._genePerOrg

    def get_genes_per_org(self, org: Organism) -> Generator[Gene, None, None]:
        """Returns the genes belonging to the gene family in the given Organism

        :param org: Organism to look for

        :return: A set of gene(s)
        """
        if len(self._genePerOrg) == 0:
            _ = self.get_org_dict()
        if org not in self._genePerOrg:
            raise KeyError(f"Organism don't belong to the gene family: {self.name}")
        for gene in self._genePerOrg[org]:
            yield gene