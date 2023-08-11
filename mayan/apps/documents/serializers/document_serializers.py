from django.utils.translation import ugettext_lazy as _

from mayan.apps.rest_api import serializers
from mayan.apps.rest_api.relations import FilteredPrimaryKeyRelatedField
from mayan.apps.storage.models import SharedUploadedFile
from mayan.apps.cabinets.serializers import CabinetDocumentAddByCabinetIdSerializer,CabinetSerializer
from mayan.apps.tags.serializers import DocumentTagAttachSerializer, TagSerializer
from mayan.apps.metadata.models import DocumentMetadata, MetadataType
from mayan.apps.metadata.permissions import (
    permission_document_metadata_add
)
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError

from ..models.document_models import Document
from ..models.document_type_models import DocumentType
from ..permissions import permission_document_change_type
from ..tasks import task_document_file_upload

from .document_file_serializers import DocumentFileSerializer
from .document_type_serializers import DocumentTypeSerializer
from .document_version_serializers import DocumentVersionSerializer


class DocumentFileActionSerializer(serializers.Serializer):
    id = serializers.CharField(
        label=_('ID'), read_only=True, source='backend_id'
    )
    label = serializers.CharField(
        label=_('Label'), read_only=True
    )


class DocumentSerializer(serializers.HyperlinkedModelSerializer):
    document_type = DocumentTypeSerializer(
        label=_('Document type'), read_only=True
    )
    document_type_id = serializers.IntegerField(
        help_text=_('Document type ID for the new document.'),
        label=_('Document type ID'), write_only=True
    )
    document_change_type_url = serializers.HyperlinkedIdentityField(
        label=_('Document change type URL'), lookup_url_kwarg='document_id',
        view_name='rest_api:document-change-type'
    )
    file_latest = DocumentFileSerializer(
        label=_('File latest'), many=False, read_only=True
    )
    file_list_url = serializers.HyperlinkedIdentityField(
        label=_('File list URL'), lookup_url_kwarg='document_id',
        view_name='rest_api:documentfile-list'
    )
    version_active = DocumentVersionSerializer(
        label=_('Version active'), many=False, read_only=True
    )
    version_list_url = serializers.HyperlinkedIdentityField(
        label=_('Version list URL'), lookup_url_kwarg='document_id',
        view_name='rest_api:documentversion-list'
    )
    # Added by Naresh
    tags=TagSerializer(many=True,read_only=True)
    cabinets=CabinetSerializer(many=True,read_only=True)
    ## --end

    class Meta:
        create_only_fields = ('document_type_id',)
        extra_kwargs = {
            'url': {
                'label': _('URL'),
                'lookup_url_kwarg': 'document_id',
                'view_name': 'rest_api:document-detail'
            },
        }
        fields = (
            'datetime_created', 'description', 'document_change_type_url',
            'document_type', 'document_type_id', 'file_latest',
            'file_list_url', 'id', 'label', 'language', 'url', 'uuid',
            'version_active', 'version_list_url','tags','cabinets'
        )
        model = Document
        read_only_fields = (
            'datetime_created', 'document_change_type_url', 'document_type',
            'file_latest', 'file_list_url', 'id', 'uuid', 'url',
            'version_list_url','tags','cabinets'
        )


class DocumentChangeTypeSerializer(serializers.Serializer):
    document_type_id = FilteredPrimaryKeyRelatedField(
        label=_('Document type ID'), help_text=_(
            'Primary key of the document type into which the document '
            'will be changed.'
        ), source_permission=permission_document_change_type,
        source_queryset_method='get_document_type_queryset', write_only=True
    )

    def get_document_type_queryset(self):
        return DocumentType.objects.exclude(
            pk=self.context['object'].document_type.pk
        )


class DocumentUploadSerializer(DocumentSerializer):
    file = serializers.FileField(
        label=_('File'), write_only=True
    )

    def create(self, validated_data):
        file = validated_data.pop('file')
        validated_data['label'] = validated_data.get('label', str(file))
        user = validated_data['_instance_extra_data']['_event_actor']
        instance = super().create(validated_data=validated_data)
        # Added by Naresh to update tags ,cabinates and metadata while uploading file
        request=self.context.get('request',None)
        tags=request.data.get('tags',None)
        # tags=[1,2]
        if tags:
            for tag in tags:
                serializer=DocumentTagAttachSerializer(context={'request':request},data={'tag':tag})
                if serializer.is_valid():
                    l_tag = serializer.validated_data['tag']
                    l_tag.attach_to(document=instance, user=request.user)

        cabinets=request.data.get('cabinets',None)
        # cabinets=[1,2,3]
        if cabinets:
            for cabinet in cabinets:
                serializer=CabinetDocumentAddByCabinetIdSerializer(context={'request':request},data={'cabinet':cabinet})
                if serializer.is_valid():
                    l_cabinet = serializer.validated_data['cabinet']
                    l_cabinet.document_add(document=instance, user=request.user)

        metadata_values=request.data.get('metadata',None)      
        # metadata_values=[{
        #     "metadata_type_id":'1',
        #     "value":"2023-08-20"
        #     }]
        if metadata_values:
            for metadata in metadata_values:
                metadata_serializer=AddDocumentMetadataSerializer(context={'request':request,'external_object':instance,"filemetadata":True},data=metadata)
                metadata_serializer.is_valid(raise_exception=False)
        ##--end by Naresh

        shared_uploaded_file = SharedUploadedFile.objects.create(file=file)

        task_document_file_upload.apply_async(
            kwargs={
                'document_id': instance.pk,
                'shared_uploaded_file_id': shared_uploaded_file.pk,
                'user_id': user.pk
            }
        )

        return instance

    class Meta(DocumentSerializer.Meta):
        create_only_fields = ('document_type_id', 'file')
        fields = (
            'datetime_created', 'description', 'document_change_type_url',
            'document_type', 'document_type_id', 'file', 'file_list_url',
            'id', 'label', 'language', 'file_latest', 'pk', 'url', 'uuid',
            'version_active', 'version_list_url'
        )

class AddDocumentMetadataSerializer(
    serializers.ModelSerializer
):
    metadata_type_id = FilteredPrimaryKeyRelatedField(
        help_text=_(
            'Primary key of the metadata type to be added to the document.'
        ), label=_('Metadata type ID'), source_model=MetadataType,
        source_permission=permission_document_metadata_add, write_only=True
    )
    document = DocumentSerializer(
        label=_('Document'), read_only=True
    )
    url = serializers.SerializerMethodField(
        label=_('URL')
    )

    class Meta:
        create_only_fields = ('metadata_type_id',)
        fields = (
            'document', 'id',  'metadata_type_id', 'url',
            'value'
        )
        model = DocumentMetadata
        read_only_fields = ('document', 'id', 'url')


    def validate(self, attrs):
        if self.instance:
            self.instance.value = attrs['value']

            try:
                self.instance.full_clean()
            except DjangoValidationError as exception:
                raise ValidationError(detail=exception)

            attrs['value'] = self.instance.value

            return attrs
        else:
            attrs['document'] = self.context['external_object']
            attrs['metadata_type'] = MetadataType.objects.get(
                pk=attrs.pop('metadata_type_id').pk
            )

            instance = DocumentMetadata(**attrs)
            try:
                instance.full_clean()
            except DjangoValidationError as exception:
                raise ValidationError(detail=exception)

            attrs['value'] = instance.value

            return attrs