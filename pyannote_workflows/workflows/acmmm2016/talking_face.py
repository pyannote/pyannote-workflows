import luigi
import sciluigi

import pyannote_workflows.tasks.speech
import pyannote_workflows.tasks.face
import pyannote_workflows.tasks.evaluation
import pyannote_workflows.tasks.tvd_dataset
import pyannote_workflows.tasks.propagation
import pyannote_workflows.utils
from pyannote.core import Segment, Annotation
import pyannote.core.json
from pprint import pprint


class _Openface(sciluigi.ExternalTask):

    workdir = luigi.Parameter()
    series = luigi.Parameter()
    season = luigi.IntParameter()
    episode = luigi.IntParameter()

    def out_put(self):
        TEMPLATE = '{workdir}/external/openface/{episode}.txt'
        path = TEMPLATE.format(
            workdir=self.workdir,
            episode=pyannote_workflows.tasks.tvd_dataset.get_episode(self))
        return sciluigi.TargetInfo(self, path)


class _Sequences(sciluigi.ExternalTask):

    workdir = luigi.Parameter()
    series = luigi.Parameter()
    season = luigi.IntParameter()
    episode = luigi.IntParameter()

    def out_put(self):
        TEMPLATE = '{workdir}/external/gregory/lists/{episode}.txt'
        path = TEMPLATE.format(
            workdir=self.workdir,
            episode=pyannote_workflows.tasks.tvd_dataset.get_episode(self))
        return sciluigi.TargetInfo(self, path)


class _TalkingFace(sciluigi.Task, pyannote_workflows.utils.AutoOutput):

    workdir = luigi.Parameter()
    exp = luigi.Parameter(default='Segmentation_0.6Pfa')
    modality = luigi.Parameter(default='AV')

    in_sequences = None

    def run(self):

        # XML files generated by Gregory
        TEMPLATE = '{workdir}/external/gregory/{exp}/{episode}.{identifier:05d}.{i:02d}.{modality}.xml'

        episode = pyannote_workflows.tasks.tvd_dataset.get_episode(
            self.in_sequences().task)

        talkingFace = Annotation()

        with self.in_sequences().open('r') as g:

            for seq_line in g:

                # for each test sequence, load Gregory's results
                # and keep only talking faces (with original face track ID)

                seq_tokens = seq_line.strip().split()
                identifier = int(seq_tokens[4])
                i = int(seq_tokens[5])
                start_time = float(seq_tokens[6])

                path = TEMPLATE.format(
                    workdir=self.workdir,
                    exp=self.exp, modality=self.modality,
                    episode=episode, identifier=identifier, i=i)

                try:
                    with open(path, 'r') as h:
                        for xml_line in h:
                            if 'SpeechSegment' not in xml_line:
                                continue
                            xml_tokens = xml_line.strip().split()
                            stime = start_time + float(xml_tokens[3].split('"')[1])
                            etime = start_time + float(xml_tokens[4].split('"')[1])
                            segment = Segment(stime, etime)
                            talkingFace[segment, identifier] = 'talking'
                except:
                    pass

        with self.out_put().open('w') as f:
            pyannote.core.json.dump(talkingFace, f)


class _TalkingFaceClustering(sciluigi.Task, pyannote_workflows.utils.AutoOutput):
    """Label talking faces with face clusters"""

    in_clusters = None
    in_talking = None

    def run(self):

        # load face clusters
        with self.in_clusters().open('r') as fp:
            clusters = pyannote.core.json.load(fp)

        # load talking faces
        with self.in_talking().open('r') as fp:
            talking = pyannote.core.json.load(fp)

        # propagate face clusters to talking face
        talkingClusters = Annotation()
        for (segment, track), (other_segment, other_track) in talking.co_iter(clusters):
            if track != other_track:
                continue
            talkingClusters[segment, track] = clusters[other_segment, other_track]

        with self.out_put().open('w') as fp:
            pyannote.core.json.dump(talkingClusters, fp)


class TalkingFace(sciluigi.WorkflowTask):

    workdir = luigi.Parameter(default='/work')
    tvddir = luigi.Parameter(default='/tvd')
    series = luigi.Parameter(default='GameOfThrones')
    season = luigi.IntParameter(default=1)
    episode = luigi.IntParameter(default=1)
    language = luigi.Parameter(default='en')

    faceClustering__threshold = luigi.FloatParameter(default=0.4)

    talkingFace__exp = luigi.Parameter(default="Segmentation_0.6Pfa")
    talkingFace__modality = luigi.Parameter(default="AV")

    bicClusteringFeatures__e = luigi.BoolParameter(default=True)
    bicClusteringFeatures__De = luigi.BoolParameter(default=False)
    bicClusteringFeatures__DDe = luigi.BoolParameter(default=False)
    bicClusteringFeatures__coefs = luigi.IntParameter(default=12)
    bicClusteringFeatures__D = luigi.BoolParameter(default=False)
    bicClusteringFeatures__DD = luigi.BoolParameter(default=False)

    bicClustering__penalty_coef = luigi.FloatParameter(default=3.5)
    bicClustering__covariance_type = luigi.Parameter(default='full')

    hyperopt = luigi.Parameter(default=None)

    def workflow(self):

        # =====================================================================
        # TALKING-FACE DETECTION
        # =====================================================================

        _sequences = self.new_task(
            '_sequences', _Sequences,
            workdir=self.workdir,
            series=self.series,
            season=self.season,
            episode=self.episode)

        _talkingFace = self.new_task(
            '_talkingFace',
            _TalkingFace,
            workdir=self.workdir,
            exp=self.talkingFace__exp,
            modality=self.talkingFace__modality)

        _talkingFace.in_sequences = _sequences.out_put

        # =====================================================================
        # TALKING-FACE CLUSTERING
        # =====================================================================

        openface = self.new_task(
            'openface',
            _Openface,
            workdir=self.workdir,
            series=self.series,
            season=self.season,
            episode=self.episode)

        precomputeFaceClustering = self.new_task(
            'precomputeFaceClustering',
            pyannote_workflows.tasks.face.PrecomputeClustering)

        precomputeFaceClustering.in_openface = openface.out_put

        faceClustering = self.new_task(
            'faceClustering',
            pyannote_workflows.tasks.face.Clustering,
            threshold=self.faceClustering__threshold)

        faceClustering.in_precomputed = precomputeFaceClustering.out_put

        _talkingFaceClustering = self.new_task(
            '_talkingFaceClustering',
            _TalkingFaceClustering,
            workdir=self.workdir)

        _talkingFaceClustering.in_clusters = faceClustering.out_put
        _talkingFaceClustering.in_talking = _talkingFace.out_put

        # =====================================================================
        # SPEECH / NON-SPEECH
        # =====================================================================

        audio = self.new_task(
            'audio',
            pyannote_workflows.tasks.tvd_dataset.Audio,
            tvddir=self.tvddir,
            series=self.series,
            season=self.season,
            episode=self.episode,
            language=self.language)

        speakerReference = self.new_task(
            'speakerReference',
            pyannote_workflows.tasks.tvd_dataset.Speaker,
            workdir=self.workdir,
            tvddir=self.tvddir,
            series=self.series,
            season=self.season,
            episode=self.episode)

        speech = self.new_task(
            'speechReference',
            pyannote_workflows.tasks.tvd_dataset.Speech,
            to_annotation=True)

        speech.in_wav = audio.out_put
        speech.in_speaker = speakerReference.out_put

        # =====================================================================
        # MERGE SPEECH TURNS SHARING SAME TALKING-FACE CLUSTER
        # =====================================================================

        conservativeDirectTagging = self.new_task(
            'conservativeDirectTagging',
            pyannote_workflows.tasks.propagation.ConservativeDirectTagging)

        conservativeDirectTagging.in_source = _talkingFaceClustering.out_put
        conservativeDirectTagging.in_target = speech.out_put

        # =====================================================================
        # BIC CLUSTERING
        # =====================================================================

        bicClusteringFeatures = self.new_task(
            'bicClusteringFeatures',
            pyannote_workflows.tasks.speech.MFCC,
            e=self.bicClusteringFeatures__e,
            De=self.bicClusteringFeatures__De,
            DDe=self.bicClusteringFeatures__DDe,
            coefs=self.bicClusteringFeatures__coefs,
            D=self.bicClusteringFeatures__D,
            DD=self.bicClusteringFeatures__DD)

        bicClusteringFeatures.in_audio = audio.out_put

        bicClustering = self.new_task(
            'bicClustering',
            pyannote_workflows.tasks.speech.BICClustering,
            penalty_coef=self.bicClustering__penalty_coef,
            covariance_type=self.bicClustering__covariance_type)

        bicClustering.in_segmentation = conservativeDirectTagging.out_put
        bicClustering.in_features = bicClusteringFeatures.out_put

        # =====================================================================
        # EVALUATION
        # =====================================================================

        evaluateDiarization = self.new_task(
            'evaluateDiarization',
            pyannote_workflows.tasks.evaluation.EvaluateDiarizationFast)

        evaluateDiarization.in_hypothesis = bicClustering.out_put
        evaluateDiarization.in_reference = speakerReference.out_put

        if hasattr(self, 'auto_output'):
            pprint(self.auto_output)

        if self.hyperopt is not None:
            hyperopt = self.new_task(
                'hyperopt',
                pyannote_workflows.utils.Hyperopt,
                temp=self.hyperopt)
            hyperopt.in_evaluation = evaluateDiarization.out_put
            return hyperopt

        else:
            return evaluateDiarization
