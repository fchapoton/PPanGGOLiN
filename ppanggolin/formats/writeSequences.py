#!/usr/bin/env python3
# coding:utf-8

# default libraries
import argparse
import logging
import re
import subprocess
from pathlib import Path
from typing import TextIO, Dict, Set, Iterable
import tempfile
import shutil

# installed libraries
from tqdm import tqdm

# local libraries
from ppanggolin.pangenome import Pangenome
from ppanggolin.geneFamily import GeneFamily
from ppanggolin.genome import Gene, Organism
from ppanggolin.utils import write_compressed_or_not, mk_outdir, read_compressed_or_not, restricted_float, detect_filetype
from ppanggolin.formats.readBinaries import check_pangenome_info, write_gene_sequences_from_pangenome_file

module_regex = re.compile(r'^module_\d+')  # \d == [0-9]
poss_values = ['all', 'persistent', 'shell', 'cloud', 'rgp', 'softcore', 'core', module_regex]
poss_values_log = f"Possible values are {', '.join(poss_values[:-1])}, module_X with X being a module id."


def check_write_sequences_args(args: argparse.Namespace) -> None:
    """Check arguments compatibility in CLI

    :param args: argparse namespace arguments from CLI

    :raises argparse.ArgumentTypeError: if region is given but neither fasta nor anno is given
    """
    if args.regions is not None and args.fasta is None and args.anno is None:
        raise argparse.ArgumentError(argument=None,
                                     message="The --regions options requires the use of --anno or --fasta "
                                             "(You need to provide the same file used to compute the pangenome)")


def check_pangenome_to_write_sequences(pangenome: Pangenome, regions: str = None, genes: str = None,
                                       genes_prot: str = None, gene_families: str = None,
                                       prot_families: str = None, disable_bar: bool = False):
    """Check and load required information from pangenome

    :param pangenome: Empty pangenome
    :param regions: Check and load the RGP
    :param genes: Check and load the genes
    :param genes_prot: Write amino acid CDS sequences.
    :param gene_families: Check and load the gene families to write representative nucleotide sequences.
    :param prot_families: Check and load the gene families to write representative amino acid sequences.
    :param disable_bar: Disable progress bar

    :raises AssertionError: if not any arguments to write any file is given
    :raises ValueError: if the given filter is not recognized
    :raises AttributeError: if the pangenome does not contain the required information
    """
    if not any(x for x in [regions, genes, genes_prot, prot_families, gene_families]):
        raise AssertionError("You did not indicate what file you wanted to write.")

    need_annotations = False
    need_families = False
    need_graph = False
    need_partitions = False
    need_spots = False
    need_regions = False
    need_modules = False

    if prot_families is not None:
        need_families = True
        if prot_families in ["core", "softcore"]:
            need_annotations = True

    if any(x is not None for x in [regions, genes, genes_prot, gene_families]):
        need_annotations = True
        need_families = True
    if regions is not None or any(x == "rgp" for x in (genes, gene_families, prot_families)):
        need_annotations = True
        need_regions = True
    if any(x in ["persistent", "shell", "cloud"] for x in (genes, gene_families, prot_families)):
        need_partitions = True
    for x in (genes, gene_families, prot_families):
        if x is not None and 'module_' in x:
            need_modules = True

    if not (need_annotations or need_families or need_graph or need_partitions or
            need_spots or need_regions or need_modules):
        # then nothing is needed, then something is wrong.
        # find which filter was provided
        provided_filter = ''
        if genes is not None:
            provided_filter = genes
        if genes_prot is not None:
            provided_filter = genes_prot
        if gene_families is not None:
            provided_filter = gene_families
        if prot_families is not None:
            provided_filter = prot_families
        if regions is not None:
            provided_filter = regions
        raise ValueError(f"The filter that you indicated '{provided_filter}' was not understood by PPanGGOLiN. "
                         f"{poss_values_log}")

    if pangenome.status["geneSequences"] not in ["inFile"] and (genes or gene_families):
        raise AttributeError("The provided pangenome has no gene sequences. "
                             "This is not compatible with any of the following options : --genes, --gene_families")
    if pangenome.status["geneFamilySequences"] not in ["Loaded", "Computed", "inFile"] and prot_families:
        raise AttributeError("The provided pangenome has no gene families. This is not compatible with any of "
                             "the following options : --prot_families, --gene_families")

    check_pangenome_info(pangenome, need_annotations=need_annotations, need_families=need_families,
                         need_graph=need_graph, need_partitions=need_partitions, need_rgp=need_regions,
                         need_spots=need_spots, need_modules=need_modules, disable_bar=disable_bar)


def write_gene_sequences_from_annotations(genes_to_write: Iterable[Gene], file_obj: TextIO, add: str = '',
                                          disable_bar: bool = False):
    """
    Writes the CDS sequences to a File object,
    and adds the string provided through `add` in front of it.
    Loads the sequences from previously computed or loaded annotations.

    :param genes_to_write: Genes to write.
    :param file_obj: Output file to write sequences.
    :param add: Add prefix to gene ID.
    :param disable_bar: Disable progress bar.
    """
    logging.getLogger("PPanGGOLiN").info(f"Writing all CDS sequences in {file_obj.name}")
    for gene in tqdm(genes_to_write, unit="gene", disable=disable_bar):
        if gene.type == "CDS":
            file_obj.write(f'>{add}{gene.ID}\n')
            file_obj.write(f'{gene.dna}\n')
    file_obj.flush()


def create_mmseqs_db(sequences: TextIO, db_name: str, tmpdir: Path, db_mode: int = 0, db_type: int = 0) -> Path:
    """Create a MMseqs2 database from a sequences file.

    :param sequences: File with the sequences
    :param db_name: name of the database
    :param tmpdir: Temporary directory to save the MMSeqs2 files
    :param db_mode: Createdb mode 0: copy data, 1: soft link data and write new index (works only with single line fasta/q)
    :param db_type: Database type 0: auto, 1: amino acid 2: nucleotides

    :return: Path to the MMSeqs2 database
    """
    assert db_mode in [0, 1], f"Createdb mode must be 0 or 1, given {db_mode}"
    assert db_type in [0, 1, 2], f"dbtype must be 0, 1 or 2, given {db_type}"

    seq_nucdb = tmpdir / db_name
    cmd = list(map(str, ["mmseqs", "createdb", "--createdb-mode", db_mode,
                         "--dbtype", db_type, sequences.name, seq_nucdb]))
    logging.getLogger("PPanGGOLiN").debug(" ".join(cmd))
    logging.getLogger("PPanGGOLiN").info("Creating sequence database...")
    subprocess.run(cmd, stdout=subprocess.DEVNULL, check=True)
    return seq_nucdb


def translate_genes(sequences: TextIO, db_name: str, tmpdir: Path, threads: int = 1,
                    is_single_line_fasta: bool = False, code: int = 11) -> Path:
    """Translate nucleotide sequences into MMSeqs2 amino acid sequences database

    :param sequences: File with the nucleotide sequences
    :param db_name: name of the output database
    :param tmpdir: Temporary directory to save the MMSeqs2 files
    :param threads: Number of available threads to use
    :param is_single_line_fasta: Allow to use soft link in MMSeqs2 database
    :param code: Translation code to use

    :return: Path to the MMSeqs2 database
    """
    seq_nucdb = create_mmseqs_db(sequences, 'nucleotide_sequences_db', tmpdir,
                                 db_mode=1 if is_single_line_fasta else 0, db_type=2)
    logging.getLogger("PPanGGOLiN").debug("Translate sequence ...")
    seqdb = tmpdir / db_name
    cmd = list(map(str, ["mmseqs", "translatenucs", seq_nucdb, seqdb, "--threads", threads, "--translation-table", code]))
    logging.getLogger("PPanGGOLiN").debug(" ".join(cmd))
    subprocess.run(cmd, stdout=subprocess.DEVNULL, check=True)
    return seqdb


def write_gene_sequences(pangenome: Pangenome, output: Path, genes: str, soft_core: float = 0.95,
                         compress: bool = False, disable_bar: bool = False):
    """
    Write all nucleotide CDS sequences

    :param pangenome: Pangenome object with gene families sequences
    :param output: Path to output directory
    :param genes: Selected partition of gene
    :param soft_core: Soft core threshold to use
    :param compress: Compress the file in .gz
    :param disable_bar: Disable progress bar

    :raises AttributeError: If the pangenome does not contain gene sequences
    """

    logging.getLogger("PPanGGOLiN").info("Writing all the gene nucleotide sequences...")
    outpath = output / f"{genes}_genes.fna"

    genefams = set()
    genes_to_write = []
    if genes == "rgp":
        logging.getLogger("PPanGGOLiN").info("Writing the gene nucleotide sequences in RGPs...")
        for region in pangenome.regions:
            genes_to_write.extend(region.genes)
    else:
        genefams = select_families(pangenome, genes, "gene nucleotide sequences", soft_core)

    for fam in genefams:
        genes_to_write.extend(fam.genes)

    logging.getLogger("PPanGGOLiN").info(f"There are {len(genes_to_write)} genes to write")
    with write_compressed_or_not(outpath, compress) as fasta:
        if pangenome.status["geneSequences"] in ["inFile"]:
            write_gene_sequences_from_pangenome_file(pangenome.file, fasta, set([gene.ID for gene in genes_to_write]),
                                                     disable_bar=disable_bar)
        elif pangenome.status["geneSequences"] in ["Computed", "Loaded"]:
            write_gene_sequences_from_annotations(genes_to_write, fasta, disable_bar=disable_bar)
        else:
            # this should never happen if the pangenome has been properly checked before launching this function.
            raise AttributeError("The pangenome does not include gene sequences")
    logging.getLogger("PPanGGOLiN").info(f"Done writing the gene sequences : '{outpath}'")


def write_gene_protein_sequences(pangenome: Pangenome, output: Path, genes_prot: str, soft_core: float = 0.95,
                                 compress: bool = False, keep_tmp: bool = False, tmp: Path = None,
                                 threads: int = 1, code: int = 11, disable_bar: bool = False):
    """ Write all amino acid sequences from given genes in pangenome

    :param pangenome: Pangenome object with gene families sequences
    :param output: Path to output directory
    :param genes_prot: Selected partition of gene
    :param soft_core: Soft core threshold to use
    :param compress: Compress the file in .gz
    :param keep_tmp: Keep temporary directory
    :param tmp: Path to temporary directory
    :param threads: Number of threads available
    :param code: Genetic code use to translate nucleotide sequences to protein sequences
    :param disable_bar: Disable progress bar
    """
    tmpdir = tmp / "translateGenes" if tmp is not None else Path(f"{tempfile.gettempdir()}/translateGenes")
    mk_outdir(tmpdir, True, True)

    write_gene_sequences(pangenome, tmpdir, genes_prot, soft_core, compress, disable_bar)

    with open(tmpdir / f"{genes_prot}_genes.fna") as sequences:
        translate_db = translate_genes(sequences, 'aa_db', tmpdir, threads, True, code)
    outpath = output / f"{genes_prot}_protein_genes.fna"
    cmd = list(map(str, ["mmseqs", "convert2fasta", translate_db, outpath]))
    logging.getLogger("PPanGGOLiN").debug(" ".join(cmd))
    subprocess.run(cmd, stdout=subprocess.DEVNULL, check=True)
    logging.getLogger("PPanGGOLiN").info(f"Done writing the gene sequences : '{outpath}'")

    if not keep_tmp:
        logging.getLogger("PPanGGOLiN").debug("Clean temporary directory")
        shutil.rmtree(tmpdir)


def select_families(pangenome: Pangenome, partition: str, type_name: str, soft_core: float = 0.95) -> Set[GeneFamily]:
    """
    function used to filter down families to the given partition

    :param pangenome: Pangenome object
    :param partition: Selected partition
    :param type_name: Which type of sequence we want. Gene families, protein, gene
    :param soft_core: Soft core threshold to use

    :return: Selected gene families
    """
    genefams = set()
    if partition == 'all':
        logging.getLogger("PPanGGOLiN").info(f"Writing all of the {type_name}...")
        genefams = pangenome.gene_families

    elif partition in ['persistent', 'shell', 'cloud']:
        logging.getLogger("PPanGGOLiN").info(f"Writing the {type_name} of the {partition}...")
        for fam in pangenome.gene_families:
            if fam.named_partition == partition:
                genefams.add(fam)

    elif partition == "rgp":
        logging.getLogger("PPanGGOLiN").info(f"Writing the {type_name} in RGPs...")
        for region in pangenome.regions:
            genefams |= set(region.families)

    elif partition == "softcore":
        logging.getLogger("PPanGGOLiN").info(
            f"Writing the {type_name} in {partition} genome, that are present in more than {soft_core} of genomes")
        threshold = pangenome.number_of_organisms * soft_core
        for fam in pangenome.gene_families:
            if fam.number_of_organisms >= threshold:
                genefams.add(fam)

    elif partition == "core":
        logging.getLogger("PPanGGOLiN").info(f"Writing the representative {type_name} of the {partition} "
                                             "gene families...")
        for fam in pangenome.gene_families:
            if fam.number_of_organisms == pangenome.number_of_organisms:
                genefams.add(fam)

    elif "module_" in partition:
        logging.getLogger("PPanGGOLiN").info(f"Writing the representation {type_name} of {partition} gene families...")
        mod_id = int(partition.replace("module_", ""))
        for mod in pangenome.modules:
            # could be way more efficient with a dict structure instead of a set
            if mod.ID == mod_id:
                genefams |= set(mod.families)
                break
    return genefams


def write_fasta_gene_fam(pangenome: Pangenome, output: Path, gene_families: str, soft_core: float = 0.95,
                         compress: bool = False, disable_bar=False):
    """
    Write representative nucleotide sequences of gene families

    :param pangenome: Pangenome object with gene families sequences
    :param output: Path to output directory
    :param gene_families: Selected partition of gene families
    :param soft_core: Soft core threshold to use
    :param compress: Compress the file in .gz
    :param disable_bar: Disable progress bar
    """

    outpath = output / f"{gene_families}_nucleotide_families.fasta"

    genefams = select_families(pangenome, gene_families, "representative nucleotide sequences of the gene families",
                               soft_core)

    with write_compressed_or_not(outpath, compress) as fasta:
        write_gene_sequences_from_pangenome_file(pangenome.file, fasta, [fam.name for fam in genefams],
                                                 disable_bar=disable_bar)

    logging.getLogger("PPanGGOLiN").info(
        f"Done writing the representative nucleotide sequences of the gene families : '{outpath}'")


def write_fasta_prot_fam(pangenome: Pangenome, output: Path, prot_families: str, soft_core: float = 0.95,
                         compress: bool = False, disable_bar: bool = False):
    """
    Write representative amino acid sequences of gene families.

    :param pangenome: Pangenome object with gene families sequences
    :param output: Path to output directory
    :param prot_families: Selected partition of protein families
    :param soft_core: Soft core threshold to use
    :param compress: Compress the file in .gz
    :param disable_bar: Disable progress bar
    """

    outpath = output / f"{prot_families}_protein_families.faa"

    genefams = select_families(pangenome, prot_families, "representative amino acid sequences of the gene families",
                               soft_core)

    with write_compressed_or_not(outpath, compress) as fasta:
        for fam in tqdm(genefams, unit="prot families", disable=disable_bar):
            fasta.write('>' + fam.name + "\n")
            fasta.write(fam.sequence + "\n")
    logging.getLogger("PPanGGOLiN").info(
        f"Done writing the representative amino acid sequences of the gene families : '{outpath}'")


def read_fasta_or_gff(file_path: Path) -> Dict[str, str]:
    """
    Read the genome file in fasta or gbff format

    :param file_path: Path to genome file

    :return: Dictionary with all sequences associated to contig
    """
    sequence_dict = {}
    seqname = ""
    seq = ""
    in_fasta_part = False
    with read_compressed_or_not(file_path) as f:
        for line in f:
            if line.startswith(">"):
                in_fasta_part = True
            if in_fasta_part:
                if line.startswith('>'):
                    if seq != "":
                        sequence_dict[seqname] = seq
                        seq = ""
                    seqname = line[1:].strip().split()[0]
                else:
                    seq += line.strip()
        if seq != "":
            sequence_dict[seqname] = seq
    return sequence_dict


def read_fasta_gbk(file_path: Path) -> Dict[str, str]:
    """
    Read the genome file in gbk format

    :param file_path: Path to genome file

    :return: Dictionary with all sequences associated to contig
    """
    # line.startswith("ORIGIN"):
    sequence_dict = {}
    lines = read_compressed_or_not(file_path).readlines()[::-1]
    contig_id, contig_locus_id = ("", "")
    while len(lines) != 0:
        line = lines.pop()
        # beginning of contig
        if line.startswith('LOCUS'):
            contig_locus_id = line.split()[1]
            # If contig_id is not specified in VERSION afterward like with Prokka,
            # in that case we use the one in LOCUS.
            while not line.startswith('FEATURES'):
                if line.startswith('VERSION'):
                    contig_id = line[12:].strip()
                line = lines.pop()
        if contig_id == "":
            contig_id = contig_locus_id
        while not line.startswith("ORIGIN"):
            line = lines.pop()  # stuff
        line = lines.pop()  # first sequence line.
        sequence = ""
        while not line.startswith('//'):
            sequence += line[10:].replace(" ", "").strip().upper()
            line = lines.pop()
        # get each gene's sequence.
        sequence_dict[contig_id] = sequence
        # end of contig
    return sequence_dict


def read_genome_file(genome_file: Path, organism: Organism) -> Dict[str, str]:
    """
    Read the genome file associated to organism to extract sequences

    :param genome_file: Path to a fasta file or gbff/gff file
    :param organism: organism object

    :return: Dictionary with all sequences associated to contig

    :raises TypeError: If the file containing sequences is not recognized
    :raises KeyError: If their inconsistency between pangenome contigs and the given contigs
    """
    filetype = detect_filetype(genome_file)
    if filetype in ["fasta", "gff"]:
        contig_to_sequence = read_fasta_or_gff(genome_file)
    elif filetype == "gbff":
        contig_to_sequence = read_fasta_gbk(genome_file)
    else:
        raise TypeError(f"Unknown filetype detected: '{genome_file}'")

    # check_contig_names
    if set(contig_to_sequence) != {contig.name for contig in organism.contigs}:
        raise KeyError(f"Contig name inconsistency detected in genome '{organism.name}' between the "
                       f"information stored in the pangenome file and the contigs found in '{genome_file}'.")

    return contig_to_sequence


def write_spaced_fasta(sequence: str, space: int = 60) -> str:
    """
    Write a maximum of element per line

    :param sequence: sequence to write
    :param space: maximum of size for one line

    :return: a sequence of maximum space character
    """
    seq = ""
    j = 0
    while j < len(sequence):
        seq += sequence[j:j + space] + "\n"
        j += space
    return seq


def write_regions_sequences(pangenome: Pangenome, output: Path, regions: str, fasta: Path = None, anno: Path = None,
                            compress: bool = False, disable_bar: bool = False):
    """
    Write representative amino acid sequences of gene families.

    :param pangenome: Pangenome object with gene families sequences
    :param output: Path to output directory
    :param regions: Write the RGP nucleotide sequences
    :param fasta: A tab-separated file listing the organism names, fasta filepath of its genomic sequences
    :param anno: A tab-separated file listing the organism names, and the gff/gbff filepath of its annotations
    :param compress: Compress the file in .gz
    :param disable_bar: Disable progress bar

    :raises SyntaxError: if no tabulation are found in list genomes file
    """
    assert fasta is not None or anno is not None, "Write regions requires to use anno or fasta, not any provided"

    organisms_file = fasta if fasta is not None else anno
    org_dict = {}
    for line in read_compressed_or_not(organisms_file):
        elements = [el.strip() for el in line.split("\t")]
        if len(elements) <= 1:
            raise SyntaxError(f"No tabulation separator found in given --fasta or --anno file: '{organisms_file}'")
        org_dict[elements[0]] = Path(elements[1])
        if not org_dict[elements[0]].exists():  # Check tsv sanity test if it's not one it's the other
            org_dict[elements[0]] = organisms_file.parent.joinpath(org_dict[elements[0]])

    logging.getLogger("PPanGGOLiN").info(f"Writing {regions} rgp genomic sequences...")
    regions_to_write = []
    if regions == "complete":
        for region in pangenome.regions:
            if not region.is_contig_border:
                regions_to_write.append(region)
    else:
        regions_to_write = pangenome.regions

    regions_to_write = sorted(regions_to_write, key=lambda x: x.organism.name)
    # order regions by organism, so that we only have to read one genome at the time

    outname = output / f"{regions}_rgp_genomic_sequences.fasta"
    with write_compressed_or_not(outname, compress) as fasta:
        loaded_genome = ""
        for region in tqdm(regions_to_write, unit="rgp", disable=disable_bar):
            if region.organism.name != loaded_genome:
                organism = region.organism
                genome_sequence = read_genome_file(org_dict[organism.name], organism)
            fasta.write(f">{region.name}\n")
            fasta.write(
                write_spaced_fasta(genome_sequence[region.contig.name][region.start:region.stop], 60))
    logging.getLogger("PPanGGOLiN").info(f"Done writing the regions nucleotide sequences: '{outname}'")


def write_sequence_files(pangenome: Pangenome, output: Path, fasta: Path = None, anno: Path = None,
                         soft_core: float = 0.95, regions: str = None, genes: str = None, genes_prot: str = None,
                         gene_families: str = None, prot_families: str = None, compress: bool = False,
                         disable_bar: bool = False, **translate_kwgs):
    """
    Main function to write sequence file from pangenome

    :param pangenome: Pangenome object containing sequences
    :param output: Path to output directory
    :param fasta: A tab-separated file listing the organism names, fasta filepath of its genomic sequences
    :param anno: A tab-separated file listing the organism names, and the gff/gbff filepath of its annotations
    :param soft_core: Soft core threshold to use
    :param regions: Write the RGP nucleotide sequences
    :param genes: Write all nucleotide CDS sequences
    :param genes_prot: Write amino acid CDS sequences.
    :param gene_families: Write representative nucleotide sequences of gene families.
    :param prot_families: Write representative amino acid sequences of gene families.
    :param compress: Compress the file in .gz
    :param disable_bar: Disable progress bar
    """

    check_pangenome_to_write_sequences(pangenome, regions, genes, genes_prot, gene_families, prot_families, disable_bar)

    if prot_families is not None:
        write_fasta_prot_fam(pangenome, output, prot_families, soft_core, compress, disable_bar)
    if gene_families is not None:
        write_fasta_gene_fam(pangenome, output, gene_families, soft_core, compress, disable_bar)
    if genes is not None:
        write_gene_sequences(pangenome, output, genes, soft_core, compress, disable_bar)
    if genes_prot is not None:
        write_gene_protein_sequences(pangenome, output, genes_prot, soft_core, compress,
                                     disable_bar=disable_bar, **translate_kwgs)
    if regions is not None:
        write_regions_sequences(pangenome, output, regions, fasta, anno, compress, disable_bar)


def launch(args: argparse.Namespace):
    """
    Command launcher

    :param args: All arguments provide by user
    """
    check_write_sequences_args(args)
    translate_kwgs = {"code": args.translation_table,
                      "threads": args.threads,
                      "tmp": args.tmpdir,
                      "keep_tmp": args.keep_tmp}
    mk_outdir(args.output, args.force)
    pangenome = Pangenome()
    pangenome.add_file(args.pangenome)
    write_sequence_files(pangenome, args.output, fasta=args.fasta, anno=args.anno, soft_core=args.soft_core,
                         regions=args.regions, genes=args.genes, genes_prot=args.genes_prot,
                         gene_families=args.gene_families, prot_families=args.prot_families, compress=args.compress,
                         disable_bar=args.disable_prog_bar, **translate_kwgs)


def subparser(sub_parser: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """
    Subparser to launch PPanGGOLiN in Command line

    :param sub_parser : sub_parser for align command

    :return : parser arguments for align command
    """
    parser = sub_parser.add_parser("fasta", formatter_class=argparse.RawTextHelpFormatter)
    parser_seq(parser)
    return parser


def filter_values(arg_value: str):
    """
    Check filter value to ensure they are in the expected format.

    :param arg_value: Argument value that is being tested.

    :return: The same argument if it is valid.

    :raises argparse.ArgumentTypeError: If the argument value is not in the expected format.
    """
    if arg_value in poss_values or module_regex.match(arg_value):
        return arg_value
    else:
        raise argparse.ArgumentTypeError(f"Invalid choice '{arg_value}'. {poss_values_log}")


def parser_seq(parser: argparse.ArgumentParser):
    """
    Parser for specific argument of fasta command

    :param parser: parser for align argument
    """

    required = parser.add_argument_group(title="Required arguments",
                                         description="One of the following arguments is required :")
    required.add_argument('-p', '--pangenome', required=False, type=Path, help="The pangenome .h5 file")
    required.add_argument('-o', '--output', required=True, type=Path,
                          help="Output directory where the file(s) will be written")

    context = parser.add_argument_group(title="Contextually required arguments",
                                        description="With --regions, the following arguments are required:")
    context.add_argument('--fasta', required=False, type=Path,
                         help="A tab-separated file listing the genome names, and the fasta filepath of its genomic "
                              "sequence(s) (the fastas can be compressed with gzip). One line per genome.")
    context.add_argument('--anno', required=False, type=Path,
                         help="A tab-separated file listing the genome names, and the gff/gbff filepath of its "
                              "annotations (the files can be compressed with gzip). One line per genome. "
                              "If this is provided, those annotations will be used.")

    onereq = parser.add_argument_group(title="Output file",
                                       description="At least one of the following argument is required. "
                                                   "Indicating 'all' writes all elements. Writing a partition "
                                                   "('persistent', 'shell' or 'cloud') write the elements associated "
                                                   "to said partition. Writing 'rgp' writes elements associated to RGPs"
                                       )
    onereq.add_argument("--genes", required=False, type=filter_values,
                        help=f"Write all nucleotide CDS sequences. {poss_values_log}")
    onereq.add_argument("--proteins", required=False, type=filter_values,
                        help=f"Write representative amino acid sequences of genes. {poss_values_log}")
    onereq.add_argument("--prot_families", required=False, type=filter_values,
                        help=f"Write representative amino acid sequences of gene families. {poss_values_log}")
    onereq.add_argument("--gene_families", required=False, type=filter_values,
                        help=f"Write representative nucleotide sequences of gene families. {poss_values_log}")

    optional = parser.add_argument_group(title="Optional arguments")
    # could make choice to allow customization
    optional.add_argument("--regions", required=False, type=str, choices=["all", "complete"],
                          help="Write the RGP nucleotide sequences (requires --anno or --fasta used to compute "
                               "the pangenome to be given)")
    optional.add_argument("--soft_core", required=False, type=restricted_float, default=0.95,
                          help="Soft core threshold to use if 'softcore' partition is chosen")
    optional.add_argument("--compress", required=False, action="store_true", help="Compress the files in .gz")
    optional.add_argument("--translation_table", required=False, default="11",
                          help="Translation table (genetic code) to use.")
    optional.add_argument("--threads", required=False, default=1, type=int, help="Number of available threads")
    optional.add_argument("--tmpdir", required=False, type=Path, default=Path(tempfile.gettempdir()),
                          help="directory for storing temporary files")
    optional.add_argument("--keep_tmp", required=False, default=False, action="store_true",
                          help="Keeping temporary files (useful for debugging).")


if __name__ == '__main__':
    """To test local change and allow using debugger"""
    from ppanggolin.utils import set_verbosity_level, add_common_arguments

    main_parser = argparse.ArgumentParser(
        description="Depicting microbial species diversity via a Partitioned PanGenome Graph Of Linked Neighbors",
        formatter_class=argparse.RawTextHelpFormatter)

    parser_seq(main_parser)
    add_common_arguments(main_parser)
    set_verbosity_level(main_parser.parse_args())
    launch(main_parser.parse_args())
