import datetime
import os

from flask import request, abort, current_app
from flask import send_file as flask_send_file
from flask_restful import Resource, reqparse
from flask_jwt_extended import jwt_required
from flask_fs.errors import FileNotFound
from zou.app import config

from zou.app.mixin import ArgsMixin
from zou.app.utils import fs
from zou.app.stores import file_store
from zou.app.services import (
    file_tree_service,
    files_service,
    persons_service,
    projects_service,
    assets_service,
    tasks_service,
    entities_service,
    user_service,
)

from zou.app.services.exception import (
    WorkingFileNotFoundException,
    OutputTypeNotFoundException,
    PersonNotFoundException,
    WrongFileTreeFileException,
    MalformedFileTreeException,
    EntryAlreadyExistsException,
)

from zou.app import (
    name_space_files,
    name_space_tasks,
    name_space_asset_instances,
    name_space_entities_not_data,
    name_space_output_type,
    name_space_entities,
    name_space_working_files,
    name_space_actions_projects,
    name_space_actions_working_files
)


def send_storage_file(working_file_id, as_attachment=False):
    """
    Send file from storage. If it's not a local storage, cache the file in
    a temporary folder before sending it. It accepts conditional headers.
    """
    prefix = "working"
    extension = "tmp"
    get_local_path = file_store.get_local_file_path
    open_file = file_store.open_file
    mimetype = "application/octet-stream"

    file_path = fs.get_file_path_and_file(
        config, get_local_path, open_file, prefix, working_file_id, extension
    )

    attachment_filename = ""
    if as_attachment:
        attachment_filename = working_file_id

    try:
        return flask_send_file(
            file_path,
            conditional=True,
            mimetype=mimetype,
            as_attachment=as_attachment,
            attachment_filename=attachment_filename,
        )
    except IOError as e:
        current_app.logger.error(e)
        return (
            {
                "error": True,
                "message": "Working file not found for: %s" % working_file_id,
            },
            404,
        )
    except FileNotFound:
        return (
            {
                "error": True,
                "message": "Working file not found for: %s" % working_file_id,
            },
            404,
        )


@name_space_working_files.route('/<working_file_id>/file')
class WorkingFileFileResource(Resource):
    """
    Allow to download and store a working file.
    """

    def check_access(self, working_file_id):
        working_file = files_service.get_working_file(working_file_id)
        task = tasks_service.get_task(working_file["task_id"])
        user_service.check_project_access(task["project_id"])
        user_service.check_entity_access(task["entity_id"])
        return working_file

    def save_uploaded_file_in_temporary_folder(self, working_file_id):
        uploaded_file = request.files["file"]
        tmp_folder = current_app.config["TMP_DIR"]
        file_name = "working-file-%s" % working_file_id
        file_path = os.path.join(tmp_folder, file_name)
        uploaded_file.save(file_path)
        return file_path

    @jwt_required
    def get(self, working_file_id):
        self.check_access(working_file_id)
        return send_storage_file(working_file_id)

    @jwt_required
    def post(self, working_file_id):
        working_file = self.check_access(working_file_id)
        file_path = self.save_uploaded_file_in_temporary_folder(
            working_file_id
        )
        file_store.add_file("working", working_file_id, file_path)
        os.remove(file_path)
        return working_file, 201


@name_space_tasks.route('/<task_id>/working-file-path')
class WorkingFilePathResource(Resource):

    @jwt_required
    def post(self, task_id):
        """
        Generate from file tree template a working file path based on several
        parameters: task, software, mode, revision and separator. Revision can be
        computed automatically as next revision if not given.
        """
        (
            name,
            mode,
            software_id,
            comment,
            revision,
            separator,
        ) = self.get_arguments()

        try:
            task = tasks_service.get_task(task_id)
            user_service.check_project_access(task["project_id"])
            user_service.check_entity_access(task["entity_id"])

            software = files_service.get_software(software_id)
            is_revision_set_by_user = revision != 0
            if not is_revision_set_by_user:
                revision = files_service.get_next_working_file_revision(
                    task_id, name
                )
            file_path = file_tree_service.get_working_folder_path(
                task,
                mode=mode,
                software=software,
                name=name,
                sep=separator,
                revision=revision,
            )
            file_name = file_tree_service.get_working_file_name(
                task,
                mode=mode,
                revision=revision,
                software=software,
                name=name,
            )
        except MalformedFileTreeException as exception:
            return (
                {"message": str(exception), "received_data": request.json},
                400,
            )

        return {"path": file_path, "name": file_name}, 200

    def get_arguments(self):
        maxsoft = files_service.get_or_create_software(
            "3ds Max", "max", ".max"
        )

        parser = reqparse.RequestParser()
        parser.add_argument("name", default="main")
        parser.add_argument("mode", default="working")
        parser.add_argument("software_id", default=maxsoft["id"])
        parser.add_argument("comment", default="")
        parser.add_argument("revision", default=0)
        parser.add_argument("sep", default="/")
        args = parser.parse_args()

        return (
            args["name"],
            args["mode"],
            args["software_id"],
            args["comment"],
            args["revision"],
            args["sep"],
        )


@name_space_entities.route('/<entity_id>/output-file-path')
class EntityOutputFilePathResource(Resource, ArgsMixin):
    """
    Generate from file tree template an output file path based on several
    parameters: entity, output type, task type, revision, mode, revision, name
    and separator. Revision can be computed automatically as next revision if
    not given.
    """

    @jwt_required
    def post(self, entity_id):
        args = self.get_arguments()
        try:
            entity = entities_service.get_entity(entity_id)
            user_service.check_project_access(entity["project_id"])
            user_service.check_entity_access(entity_id)
            output_type = files_service.get_output_type(args["output_type_id"])
            task_type = tasks_service.get_task_type(args["task_type_id"])
            entity = entities_service.get_entity(entity_id)

            is_revision_set_by_user = args["revision"] != 0
            if not is_revision_set_by_user:
                revision = files_service.get_next_output_file_revision(
                    entity_id, args["name"]
                )
            else:
                revision = args["revision"]

            folder_path = file_tree_service.get_output_folder_path(
                entity,
                mode=args["mode"],
                output_type=output_type,
                task_type=task_type,
                name=args["name"],
                representation=args["representation"],
                sep=args["separator"],
                revision=args["revision"],
            )
            file_name = file_tree_service.get_output_file_name(
                entity,
                mode=args["mode"],
                revision=revision,
                output_type=output_type,
                task_type=task_type,
                name=args["name"],
            )
        except MalformedFileTreeException as exception:
            return (
                {"message": str(exception), "received_data": request.json},
                400,
            )

        return {"folder_path": folder_path, "file_name": file_name}, 200

    def get_arguments(self):
        return self.get_args(
            [
                ("name", "main", False),
                ("mode", "output", False),
                ("output_type_id", None, True),
                ("task_type_id", None, True),
                ("revision", 0, False),
                ("extension", "", False),
                ("representation", "", False),
                ("separator", "/", False),
            ]
        )


@name_space_asset_instances.route('/<asset_instance_id>')
@name_space_entities_not_data.route('/<temporal_entity_id>/output-file-path')
class InstanceOutputFilePathResource(Resource, ArgsMixin):

    @jwt_required
    def post(self, asset_instance_id, temporal_entity_id):
        """
        Generate from file tree template an output file path based on several
        parameters: asset instance, output type, task type, revision, mode,
        revision, name and separator. Revision can be computed automatically as next
        revision in case no revision is given in parameter.
        """
        args = self.get_arguments()

        try:
            asset_instance = assets_service.get_asset_instance(
                asset_instance_id
            )
            entity = entities_service.get_entity(temporal_entity_id)
            asset = assets_service.get_asset(asset_instance["asset_id"])
            output_type = files_service.get_output_type(args["output_type_id"])
            task_type = tasks_service.get_task_type(args["task_type_id"])
            user_service.check_project_access(asset["project_id"])
            user_service.check_entity_access(asset["id"])

            folder_path = file_tree_service.get_instance_folder_path(
                asset_instance,
                entity,
                output_type=output_type,
                task_type=task_type,
                mode=args["mode"],
                name=args["name"],
                representation=args["representation"],
                revision=args["revision"],
                sep=args["separator"],
            )
            file_name = file_tree_service.get_instance_file_name(
                asset_instance,
                entity,
                output_type=output_type,
                task_type=task_type,
                mode=args["mode"],
                name=args["name"],
                revision=args["revision"],
            )
        except MalformedFileTreeException as exception:
            return (
                {"message": str(exception), "received_data": request.json},
                400,
            )

        return {"folder_path": folder_path, "file_name": file_name}, 200

    def get_arguments(self):
        return self.get_args(
            [
                ("name", "main", False),
                ("mode", "output", False),
                ("output_type_id", None, True),
                ("task_type_id", None, True),
                ("revision", 0, False),
                ("extension", "", False),
                ("representation", "", False),
                ("separator", "/", False),
            ]
        )


@name_space_tasks.route('/<task_id>/working-files/last-revisions')
class LastWorkingFilesResource(Resource):
    """
    Return last working files revision for each file name for given task.
    """

    @jwt_required
    def get(self, task_id):
        result = {}
        task = tasks_service.get_task(task_id)
        user_service.check_project_access(task["project_id"])
        user_service.check_entity_access(task["entity_id"])
        result = files_service.get_last_working_files_for_task(task["id"])

        return result


@name_space_tasks.route('/<task_id>/working-files')
class TaskWorkingFilesResource(Resource):

    @jwt_required
    def get(self, task_id):
        """
        Return all working file revisions for a given task.
        """
        result = {}
        task = tasks_service.get_task(task_id)
        user_service.check_project_access(task["project_id"])
        user_service.check_entity_access(task["entity_id"])
        result = files_service.get_working_files_for_task(task["id"])

        return result

@name_space_tasks.route('/<task_id>/working-files/new')
class NewWorkingFileResource(Resource):

    @jwt_required
    def post(self, task_id):
        """
        A working file is a file used to produce output files. 
        It is the file the CG artist is working on. It is versioned, tied to a task and a software and
        requires a comment each time it is created.
        A path is generated for each file created. The path format is defined
        in the file tree template file.
        """
        (
            name,
            mode,
            description,
            comment,
            person_id,
            software_id,
            revision,
            sep,
        ) = self.get_arguments()

        try:
            task = tasks_service.get_task(task_id)
            user_service.check_project_access(task["project_id"])
            user_service.check_entity_access(task["entity_id"])
            software = files_service.get_software(software_id)
            tasks_service.assign_task(
                task_id, persons_service.get_current_user()["id"]
            )

            if revision == 0:
                revision = files_service.get_next_working_revision(
                    task_id, name
                )

            path = self.build_path(task, name, revision, software, sep, mode)

            working_file = files_service.create_new_working_revision(
                task_id,
                person_id,
                software_id,
                name=name,
                path=path,
                comment=comment,
                revision=revision,
            )
        except EntryAlreadyExistsException:
            return {"error": "The given working file already exists."}, 400

        return working_file, 201

    def build_path(self, task, name, revision, software, sep, mode):
        folder_path = file_tree_service.get_working_folder_path(
            task, name=name, software=software, mode=mode, revision=revision
        )
        file_name = file_tree_service.get_working_file_name(
            task, name=name, software=software, revision=revision, mode=mode
        )
        return "%s%s%s" % (folder_path, sep, file_name)

    def get_arguments(self):
        person = persons_service.get_current_user()
        maxsoft = files_service.get_or_create_software(
            "3ds Max", "max", ".max"
        )

        parser = reqparse.RequestParser()
        parser.add_argument(
            "name", help="The asset name is required.", required=True
        )
        parser.add_argument("description", default="")
        parser.add_argument("mode", default="working")
        parser.add_argument("comment", default="")
        parser.add_argument("person_id", default=person["id"])
        parser.add_argument("software_id", default=maxsoft["id"])
        parser.add_argument("revision", default=0, type=int)
        parser.add_argument("sep", default="/")
        args = parser.parse_args()
        return (
            args["name"],
            args["mode"],
            args["description"],
            args["comment"],
            args["person_id"],
            args["software_id"],
            args["revision"],
            args["sep"],
        )


@name_space_actions_working_files.route('/<working_file_id>/modified')
class ModifiedFileResource(Resource):
    @jwt_required
    def put(self, working_file_id):
        """
        Update working file modification date with current date.
        """
        
        working_file = files_service.get_working_file(working_file_id)
        task = tasks_service.get_task(working_file["task_id"])
        user_service.check_project_access(task["project_id"])
        user_service.check_entity_access(task["entity_id"])
        working_file = files_service.update_working_file(
            working_file_id, {"updated_at": datetime.datetime.utcnow()}
        )
        return working_file


@name_space_actions_working_files.route('/<working_file_id>/comment')
class CommentWorkingFileResource(Resource):

    @jwt_required
    def put(self, working_file_id):
        """
        Update comment on given working file.
        """
        comment = self.get_comment_from_args()
        working_file = files_service.get_working_file(working_file_id)
        task = tasks_service.get_task(working_file["task_id"])
        user_service.check_project_access(task["project_id"])
        user_service.check_entity_access(task["entity_id"])
        working_file = self.update_comment(working_file_id, comment)
        return working_file

    def get_comment_from_args(self):
        parser = reqparse.RequestParser()
        parser.add_argument(
            "comment", required=True, help="Comment field expected."
        )
        args = parser.parse_args()
        comment = args["comment"]
        return comment

    def update_comment(self, working_file_id, comment):
        working_file = files_service.update_working_file(
            working_file_id, {"comment": comment}
        )
        return working_file


@name_space_entities.route('/<entity_id>/output-files/new')
class NewEntityOutputFileResource(Resource, ArgsMixin):
    """
    Output files are linked to entities. Each time a CG artist is satisfied
    by what he did on a working file, he can create an output file that
    will be linked to a target entity (an asset, a shot, a sequence, ...).
    It keeps track of the working file at the origin of the output file.
    An output type is required for better categorization (textures, caches,
    ...). A task type can be set too to give the department related to the
    output file.

    Revision is automatically set.
    """

    @jwt_required
    def post(self, entity_id):
        args = self.get_arguments()

        try:
            revision = int(args["revision"])

            try:
                working_file = files_service.get_working_file(
                    args["working_file_id"]
                )
                working_file_id = working_file["id"]
            except WorkingFileNotFoundException:
                working_file_id = None

            entity = entities_service.get_entity(entity_id)
            user_service.check_project_access(entity["project_id"])
            output_type = files_service.get_output_type(args["output_type_id"])
            task_type = tasks_service.get_task_type(args["task_type_id"])

            if args["person_id"] is None:
                person = persons_service.get_current_user()
            else:
                person = persons_service.get_person(args["person_id"])

            output_file = files_service.create_new_output_revision(
                entity_id,
                working_file_id,
                output_type["id"],
                person["id"],
                args["task_type_id"],
                revision=revision,
                name=args["name"],
                comment=args["comment"],
                representation=args["representation"],
                extension=args["extension"],
                nb_elements=int(args["nb_elements"]),
                file_status_id=args["file_status_id"],
            )

            output_file_dict = self.add_path_info(
                output_file,
                "output",
                entity,
                output_type,
                task_type=task_type,
                name=args["name"],
                extension=args["extension"],
                representation=args["representation"],
                separator=args["sep"],
                nb_elements=int(args["nb_elements"]),
            )
        except OutputTypeNotFoundException:
            return {"error": "Cannot find given output type."}, 400
        except PersonNotFoundException:
            return {"error": "Cannot find given person."}, 400
        except EntryAlreadyExistsException:
            return {"error": "The given output file already exists."}, 400

        return output_file_dict, 201

    def get_arguments(self):
        return self.get_args(
            [
                ("name", "main", False),
                ("mode", "output", False),
                ("output_type_id", None, True),
                ("task_type_id", None, True),
                ("person_id", None, False),
                ("working_file_id", None, False),
                ("comment", "", True),
                ("revision", 0, False),
                ("extension", "", False),
                ("representation", "", False),
                ("nb_elements", 1, False),
                ("sep", "/", False),
                ("file_status_id", None, False),
            ]
        )

    def add_path_info(
        self,
        output_file,
        mode,
        entity,
        output_type,
        task_type=None,
        name="main",
        extension="",
        representation="",
        nb_elements=1,
        separator="/",
    ):
        folder_path = file_tree_service.get_output_folder_path(
            entity,
            mode=mode,
            output_type=output_type,
            task_type=task_type,
            revision=output_file["revision"],
            representation=representation,
            name=name,
            sep=separator,
        )
        file_name = file_tree_service.get_output_file_name(
            entity,
            mode=mode,
            revision=output_file["revision"],
            output_type=output_type,
            task_type=task_type,
            name=name,
            nb_elements=nb_elements,
        )

        output_file = files_service.update_output_file(
            output_file["id"],
            {
                "path": "%s%s%s%s"
                % (folder_path, separator, file_name, extension)
            },
        )

        output_file.update(
            {"folder_path": folder_path, "file_name": file_name}
        )

        return output_file


@name_space_asset_instances.route('/<asset_instance_id>')
@name_space_entities_not_data.route('/<temporal_entity_id>/output-files/new')
class NewInstanceOutputFileResource(Resource, ArgsMixin):
    """
    Some output files are linked to assets through an instance of this asset
    for a give shot. Each time a CG artist is satisfied by what he did on a
    working file, he can create an output file that
    will be linked to a target instance.
    It keeps track of the working file at the origin of the output file.
    An output type is required for better categorization (textures, caches,
    ...). A task type can be set too to give the department related to the
    output file.
    """

    @jwt_required
    def post(self, asset_instance_id, temporal_entity_id):
        args = self.get_arguments()

        try:
            revision = int(args["revision"])
            try:
                working_file = files_service.get_working_file(
                    args["working_file_id"]
                )
                working_file_id = working_file["id"]
            except WorkingFileNotFoundException:
                working_file_id = None

            asset_instance = assets_service.get_asset_instance(
                asset_instance_id
            )
            temporal_entity = entities_service.get_entity(temporal_entity_id)

            entity = assets_service.get_asset(asset_instance["asset_id"])
            user_service.check_project_access(entity["project_id"])

            output_type = files_service.get_output_type(args["output_type_id"])
            task_type = tasks_service.get_task_type(args["task_type_id"])
            if args["person_id"] is None:
                person = persons_service.get_current_user()
            else:
                person = persons_service.get_person(args["person_id"])

            output_file = files_service.create_new_output_revision(
                asset_instance["asset_id"],
                working_file_id,
                output_type["id"],
                person["id"],
                task_type["id"],
                asset_instance_id=asset_instance["id"],
                temporal_entity_id=temporal_entity_id,
                revision=revision,
                name=args["name"],
                representation=args["representation"],
                comment=args["comment"],
                nb_elements=int(args["nb_elements"]),
                extension=args["extension"],
                file_status_id=args["file_status_id"],
            )

            output_file_dict = self.add_path_info(
                output_file,
                "output",
                asset_instance,
                temporal_entity,
                output_type,
                task_type=task_type,
                name=args["name"],
                extension=args["extension"],
                representation=args["representation"],
                nb_elements=int(args["nb_elements"]),
                separator=args["sep"],
            )
        except OutputTypeNotFoundException:
            return {"message": "Cannot find given output type."}, 400
        except PersonNotFoundException:
            return {"message": "Cannot find given person."}, 400
        except EntryAlreadyExistsException:
            return {"message": "The given output file already exists."}, 400

        return output_file_dict, 201

    def get_arguments(self):
        return self.get_args(
            [
                ("name", "main", False),
                ("mode", "output", False),
                ("output_type_id", None, True),
                ("task_type_id", None, True),
                ("person_id", None, False),
                ("working_file_id", None, False),
                ("comment", "", True),
                ("revision", 0, False),
                ("extension", "", False),
                ("representation", "", False),
                ("is_sequence", False, False),
                ("nb_elements", 1, False),
                ("sep", "/", False),
                ("file_status_id", None, False),
            ]
        )

    def add_path_info(
        self,
        output_file,
        mode,
        asset_instance,
        temporal_entity,
        output_type,
        task_type=None,
        name="main",
        extension="",
        representation="",
        nb_elements=1,
        separator="/",
    ):
        folder_path = file_tree_service.get_instance_folder_path(
            asset_instance,
            temporal_entity,
            mode=mode,
            output_type=output_type,
            revision=output_file["revision"],
            task_type=task_type,
            representation=representation,
            name=name,
            sep=separator,
        )
        file_name = file_tree_service.get_instance_file_name(
            asset_instance,
            temporal_entity,
            mode=mode,
            revision=output_file["revision"],
            output_type=output_type,
            task_type=task_type,
            name=name,
        )

        output_file = files_service.update_output_file(
            output_file["id"],
            {
                "path": "%s%s%s%s"
                % (folder_path, separator, file_name, extension)
            },
        )

        output_file.update(
            {"folder_path": folder_path, "file_name": file_name}
        )

        return output_file


@name_space_entities.route('/<entity_id>/output-files/next-revision')
class GetNextEntityOutputFileRevisionResource(Resource, ArgsMixin):

    @jwt_required
    def post(self, entity_id):
        """
        Get next revision for given entity, output type, task type and name.
        """
        args = self.get_arguments()
        entity = entities_service.get_entity(entity_id)
        output_type = files_service.get_output_type(args["output_type_id"])
        task_type = tasks_service.get_task_type(args["task_type_id"])
        user_service.check_project_access(entity["project_id"])

        next_revision_number = files_service.get_next_output_file_revision(
            entity["id"], output_type["id"], task_type["id"], args["name"]
        )

        return {"next_revision": next_revision_number}, 200

    def get_arguments(self):
        return self.get_args(
            [
                ("name", "main", False),
                ("output_type_id", None, True),
                ("task_type_id", None, True),
            ]
        )

@name_space_asset_instances.route('/<asset_instance_id>')
@name_space_entities_not_data.route('/<temporal_entity_id>/output-files/next-revision')
class GetNextInstanceOutputFileRevisionResource(Resource, ArgsMixin):

    @jwt_required
    def post(self, asset_instance_id, temporal_entity_id):
        """
        Get next revision for given asset instance, output type, task type and name.
        """
        args = self.get_arguments()

        asset_instance = assets_service.get_asset_instance(asset_instance_id)
        asset = entities_service.get_entity(asset_instance["asset_id"])
        output_type = files_service.get_output_type(args["output_type_id"])
        task_type = tasks_service.get_task_type(args["task_type_id"])
        user_service.check_project_access(asset["project_id"])

        next_revision_number = files_service.get_next_output_file_revision(
            asset["id"],
            output_type["id"],
            task_type["id"],
            args["name"],
            asset_instance_id=asset_instance["id"],
            temporal_entity_id=temporal_entity_id,
        )

        return {"next_revision": next_revision_number}, 200

    def get_arguments(self):
        return self.get_args(
            [
                ("name", "main", False),
                ("output_type_id", None, True),
                ("task_type_id", None, True),
            ]
        )


@name_space_entities.route('/<entity_id>/output-files/last-revisions')
class LastEntityOutputFilesResource(Resource):
    """
    Last revisions of output files for given entity grouped by output type
    and file name.
    """

    @jwt_required
    def get(self, entity_id):
        entity = entities_service.get_entity(entity_id)
        user_service.check_project_access(entity["project_id"])

        return files_service.get_last_output_files_for_entity(
            entity["id"],
            output_type_id=request.args.get("output_type_id", None),
            task_type_id=request.args.get("task_type_id", None),
            representation=request.args.get("representation", None),
            file_status_id=request.args.get("file_status_id", None),
        )


@name_space_asset_instances.route('/<asset_instance_id>')
@name_space_entities_not_data.route('/<temporal_entity_id>/output-files/last-revisions')
class LastInstanceOutputFilesResource(Resource):

    @jwt_required
    def get(self, asset_instance_id, temporal_entity_id):
        """
        Last revisions of output files for given instance grouped by output type
        and file name.
        """
        asset_instance = assets_service.get_asset_instance(asset_instance_id)
        entity = entities_service.get_entity(asset_instance["asset_id"])
        user_service.check_project_access(entity["project_id"])

        return files_service.get_last_output_files_for_instance(
            asset_instance["id"],
            temporal_entity_id,
            output_type_id=request.args.get("output_type_id", None),
            task_type_id=request.args.get("task_type_id", None),
            representation=request.args.get("representation", None),
            file_status_id=request.args.get("file_status_id", None),
        )


@name_space_entities.route('/<entity_id>/output-types')
class EntityOutputTypesResource(Resource):
    """
    Return all types of output generated for given entity.
    """

    @jwt_required
    def get(self, entity_id):
        entity = entities_service.get_entity(entity_id)
        user_service.check_project_access(entity["project_id"])
        return files_service.get_output_types_for_entity(entity_id)


@name_space_asset_instances.route('/<asset_instance_id>')
@name_space_entities_not_data.route('/<temporal_entity_id>/output-types')
class InstanceOutputTypesResource(Resource):

    @jwt_required
    def get(self, asset_instance_id, temporal_entity_id):
        """
        Return all types of output generated for given instance.
        """
        asset_instance = assets_service.get_asset_instance(asset_instance_id)
        entity = entities_service.get_entity(asset_instance["asset_id"])
        user_service.check_project_access(entity["project_id"])
        return files_service.get_output_types_for_instance(
            asset_instance_id, temporal_entity_id
        )


@name_space_entities.route('/<entity_id>/output-types/<output_type_id>/output-files')
class EntityOutputTypeOutputFilesResource(Resource):

    @jwt_required
    def get(self, entity_id, output_type_id):
        """
        Get all output files for given entity and given output type.
        """
        representation = request.args.get("representation", None)

        entity = entities_service.get_entity(entity_id)
        files_service.get_output_type(output_type_id)
        user_service.check_project_access(entity["project_id"])
        output_files = (
            files_service.get_output_files_for_output_type_and_entity(
                entity_id, output_type_id, representation=representation
            )
        )

        return output_files


@name_space_asset_instances.route('/<asset_instance_id>')
@name_space_entities_not_data.route('/<temporal_entity_id>/output-types')
@name_space_output_type.route('/')
class InstanceOutputTypeOutputFilesResource(Resource):

    @jwt_required
    def get(self, asset_instance_id, temporal_entity_id, output_type_id):
        """
        Get all output files for given asset instance and given output type.
        """
        representation = request.args.get("representation", None)

        asset_instance = assets_service.get_asset_instance(asset_instance_id)
        asset = assets_service.get_asset(asset_instance["asset_id"])
        user_service.check_project_access(asset["project_id"])

        files_service.get_output_type(output_type_id)
        return (
            files_service.get_output_files_for_output_type_and_asset_instance(
                asset_instance_id,
                temporal_entity_id,
                output_type_id,
                representation=representation,
            )
        )


@name_space_entities.route('/<entity_id>/output-files')
class EntityOutputFilesResource(Resource):
    """
    Get all output files for given asset instance and given output type.
    """

    @jwt_required
    def get(self, entity_id):
        entity = entities_service.get_entity(entity_id)
        user_service.check_project_access(entity["project_id"])

        task_type_id = request.args.get("task_type_id")
        output_type_id = request.args.get("output_type_id")
        name = request.args.get("name")
        representation = request.args.get("representation")
        file_status_id = request.args.get("file_status_id")

        return files_service.get_output_files_for_entity(
            entity["id"],
            task_type_id=task_type_id,
            output_type_id=output_type_id,
            name=name,
            representation=representation,
            file_status_id=file_status_id,
        )


@name_space_asset_instances.route('/<asset_instance_id>/output-files')
class InstanceOutputFilesResource(Resource):

    @jwt_required
    def get(self, asset_instance_id):
        """
        Get all output files for given asset instance and given output type.
        """
        asset_instance = assets_service.get_asset_instance(asset_instance_id)
        asset = assets_service.get_asset(asset_instance["asset_id"])
        user_service.check_project_access(asset["project_id"])

        temporal_entity_id = request.args.get("temporal_entity_id")
        task_type_id = request.args.get("task_type_id")
        output_type_id = request.args.get("output_type_id")
        name = request.args.get("name")
        representation = request.args.get("representation")
        file_status_id = request.args.get("file_status_id")

        return files_service.get_output_files_for_instance(
            asset_instance["id"],
            temporal_entity_id=temporal_entity_id,
            task_type_id=task_type_id,
            output_type_id=output_type_id,
            name=name,
            representation=representation,
            file_status_id=file_status_id,
        )


@name_space_files.route('/<file_id>')
class FileResource(Resource):

    @jwt_required
    def get(self, file_id):
        """
        Get information about a file that could be a working file as much as an
        output file.
        """
        try:
            file_dict = files_service.get_working_file(file_id)
            task = tasks_service.get_task(file_dict["task_id"])
            project_id = task["project_id"]
        except WorkingFileNotFoundException:
            file_dict = files_service.get_output_file(file_id)
            entity = entities_service.get_entity(file_dict["entity_id"])
            project_id = entity["project_id"]

        user_service.check_project_access(project_id)
        return file_dict


@name_space_actions_projects.route('/<project_id>/set-file-tree')
class SetTreeResource(Resource):
    """
    Define a template file to use for given project. Template files are located
    on the server side. Each template has a name which means that you just have
    to give a name to "select" the template to link with the project.
    """

    @jwt_required
    def post(self, project_id):
        tree_name = self.get_arguments()

        try:
            user_service.check_project_access(project_id)
            tree = file_tree_service.get_tree_from_file(tree_name)
            project = projects_service.update_project(
                project_id, {"file_tree": tree}
            )
        except WrongFileTreeFileException:
            abort(400, "Selected tree is not available")

        return project

    def get_arguments(self):
        parser = reqparse.RequestParser()
        parser.add_argument(
            "tree_name",
            help="The name of the tree to set is required.",
            required=True,
        )
        args = parser.parse_args()

        return args.get("tree_name", "")


@name_space_entities.route('/<entity_id>/working-files')
class EntityWorkingFilesResource(Resource):

    @jwt_required
    def get(self, entity_id):
        """
        Get all working files for a given entity and possibly a task and a name
        """
        task_id = request.args.get("task_id", None)
        name = request.args.get("name", None)
        relations = request.args.get("relations", False)

        entity = entities_service.get_entity(entity_id)
        user_service.check_project_access(entity["project_id"])

        return files_service.get_working_files_for_entity(
            entity_id, task_id=task_id, name=name, relations=relations
        )
